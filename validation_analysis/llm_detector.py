# ============================================================================
# Deliverable 3/4 - LLM-driven validation DETECTION engine.
#
# LLM replacement for the deterministic validation_analysis.ValidationAnalyzer.
# It builds a per-column evidence context from the Deliverable 1 canonical
# model, asks the LLM (domain knowledge of the source payroll format) which
# validations each column needs, PRINTS the raw LLM reply, and adapts the LLM's
# decisions into the SAME ValidationAnalysisResult the deterministic analyzer
# produces - so the existing Deliverable 4 IR builder + compiler still emit the
# exact 30-key ``validation-rules`` JSON with correct UUID wiring.
#
# No static per-column rules live here: the LLM decides dataType + validations;
# this module only TRANSLATES those decisions onto the closed catalogue.
# ============================================================================
"""
validation_analysis.llm_detector
=================================
LLM-driven validation-rule detection over the canonical model.

Flow::

    canonical model
        -> build_columns_context()   # per-column evidence (col_id, samples...)
        -> LLM (generate_long_response)   # domain-knowledge detection (async)
        -> [print raw reply]
        -> build_result_from_reply() # LLM JSON -> ValidationAnalysisResult
        -> (downstream) ValidationIRBuilder -> ValidationRuleCompiler -> JSON

The LLM references columns by the integer ``col_id`` this module assigns, so it
never has to echo canonical UUIDs; ``col_id`` is resolved back to the canonical
column (and its UUID) here.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from canonical.enums import IoRole
from canonical.models import (
    CanonicalAnalysisModel,
    CanonicalColumn,
    CanonicalFile,
    CanonicalSheet,
)
from contract.enums import ContractDataType
from validation_analysis.analyzer import ValidationAnalyzer
from validation_analysis.catalog import get_spec
from validation_analysis.enums import (
    RequirementSource,
    SupportedValidation,
    SupportStatus,
)
from validation_analysis.models import (
    AnalysisSummary,
    ColumnValidationProfile,
    ExpectedColumn,
    ExpectedStructure,
    ValidationAnalysisResult,
    ValidationRequirement,
    ValidationRuleInventory,
)
from prompts.validation_detection_prompt import (
    VALIDATION_DETECTION_SYSTEM_PROMPT,
    VALIDATION_DETECTION_USER_PROMPT,
)

logger = logging.getLogger(__name__)

# Max sample values per column handed to the LLM (keep the prompt bounded).
_MAX_SAMPLES = 12

# LLM ``dataType`` token -> contract data type.
_DATATYPE_TOKENS: dict[str, ContractDataType] = {
    "text": ContractDataType.TEXT,
    "number": ContractDataType.NUMBER,
    "date": ContractDataType.DATE,
    "email": ContractDataType.EMAIL,
    "dropdown": ContractDataType.DROPDOWN,
    "regex": ContractDataType.REGEX,
}

# LLM ``kind`` token -> supported validation.
_KIND_TOKENS: dict[str, SupportedValidation] = {
    "mandatory": SupportedValidation.MANDATORY,
    "unique": SupportedValidation.UNIQUE,
    "date_format": SupportedValidation.DATE_FORMAT,
    "numeric_format": SupportedValidation.NUMERIC_FORMAT,
    "email_format": SupportedValidation.EMAIL_FORMAT,
    "regex_pattern": SupportedValidation.REGEX_PATTERN,
    "list_of_values": SupportedValidation.LIST_OF_VALUES,
    "special_char_not_allowed": SupportedValidation.SPECIAL_CHAR_NOT_ALLOWED,
    "min_length": SupportedValidation.MIN_LENGTH,
    "max_length": SupportedValidation.MAX_LENGTH,
    "min_value": SupportedValidation.MIN_VALUE,
    "max_value": SupportedValidation.MAX_VALUE,
    "string_check": SupportedValidation.STRING_CHECK,
    "conditional": SupportedValidation.CONDITIONAL,
    "required_in_error_file": SupportedValidation.REQUIRED_IN_ERROR_FILE,
}

# Validation kinds the LLM overlay is ALLOWED to contribute. Everything else
# (dataType, length bounds, value bounds, uniqueness) is owned by the
# deterministic base so the two layers never fight over the same field. These
# are the domain-knowledge kinds that column statistics cannot supply.
_LLM_OVERLAY_KINDS: frozenset[SupportedValidation] = frozenset({
    SupportedValidation.MANDATORY,
    SupportedValidation.LIST_OF_VALUES,
    SupportedValidation.SPECIAL_CHAR_NOT_ALLOWED,
    SupportedValidation.DATE_FORMAT,
    SupportedValidation.REGEX_PATTERN,
    SupportedValidation.EMAIL_FORMAT,
    SupportedValidation.STRING_CHECK,
    SupportedValidation.CONDITIONAL,
    SupportedValidation.REQUIRED_IN_ERROR_FILE,
})


class LLMValidationDetectionError(RuntimeError):
    """Raised when the LLM reply cannot be parsed into a validation result."""


class LLMValidationDetector:
    """Detect validation rules per column via the LLM (domain-knowledge driven)."""

    def __init__(self, llm_client: Optional[Any] = None) -> None:
        """Args:
        llm_client: An object exposing ``async generate_long_response(system_prompt,
            context, ...) -> str`` (e.g. :class:`llm_client.LLMClient`). Injectable
            so tests can pass a mock. When ``None`` a real client is created lazily
            in :meth:`detect` (requires network/credentials).
        """
        self._llm = llm_client
        # Deterministic (non-LLM) base engine. It derives the bulk of the
        # validations - dataType, mandatory, length bounds, uniqueness - straight
        # from the column statistics already in the canonical model. The LLM is
        # then layered on top ONLY for domain knowledge the statistics cannot
        # supply (named LOV catalogues, dropdown/date/email/regex elevation,
        # disallowed-character rules).
        self._analyzer = ValidationAnalyzer()

    # -- public: full async detection --------------------------------------
    async def detect(
        self,
        model: CanonicalAnalysisModel,
        *,
        bu: str = "",
        platform: str = "",
        data_category: str = "",
        session_id: str = "",
        max_tokens: int = 16000,
        timeout: int = 600,
        print_reply: bool = True,
    ) -> "tuple[str, ValidationAnalysisResult]":
        """Run LLM validation detection over a canonical model.

        Returns:
            ``(raw_llm_reply, ValidationAnalysisResult)``. The raw reply is also
            printed when ``print_reply`` is True (user requirement).
        """
        columns_ctx, index = self.build_columns_context(model)
        # Strip the internal ``uuid`` (alignment/eval only) from the LLM payload.
        llm_columns = [
            {k: v for k, v in row.items() if k != "uuid"} for row in columns_ctx
        ]
        user_prompt = VALIDATION_DETECTION_USER_PROMPT.format(
            bu=bu or "(unspecified)",
            platform=platform or "(unspecified)",
            data_category=data_category or "(unspecified)",
            file_count=len({c["file"] for c in columns_ctx}),
            column_count=len(columns_ctx),
            columns_json=json.dumps(llm_columns, ensure_ascii=False, indent=2),
        )

        if print_reply:
            # Manual step of validation: show exactly what INPUTS/JSON we send
            # to the LLM (identity + per-column evidence) before detection runs.
            print("=" * 78)
            print("[llm-validation] DETECTION INPUTS (sent to LLM)")
            print("=" * 78)
            print(
                f"bu={bu or '(unspecified)'} | "
                f"platform={platform or '(unspecified)'} | "
                f"dataCategory={data_category or '(unspecified)'} | "
                f"session_id={session_id or '(none)'}"
            )
            print(
                f"files={len({c['file'] for c in columns_ctx})} | "
                f"columns={len(columns_ctx)}"
            )
            print("-" * 78)
            print("[llm-validation] COLUMNS JSON (LLM payload)")
            print("-" * 78)
            print(json.dumps(llm_columns, ensure_ascii=False, indent=2))
            print("=" * 78)

        llm = self._llm
        if llm is None:  # lazy import to avoid credential/network cost when mocked
            from llm_client import LLMClient

            llm = LLMClient(session_id=session_id)

        logger.info(
            "[llm-validation] requesting detection for %d columns", len(columns_ctx)
        )
        reply = await llm.generate_long_response(
            system_prompt=VALIDATION_DETECTION_SYSTEM_PROMPT,
            context=user_prompt,
            max_tokens=max_tokens,
            timeout=timeout,
        )

        # Always log the reply size + a bounded preview (first 1000 chars) so the
        # LLM output is visible in the server logs even when print_reply=False.
        reply_text = reply or ""
        logger.info(
            "[llm-validation] LLM reply: %d chars; first 1000 chars:\n%s",
            len(reply_text),
            reply_text[:1000],
        )

        if print_reply:
            print("=" * 78)
            print("[llm-validation] RAW LLM REPLY")
            print("=" * 78)
            print(reply)
            print("=" * 78)

        result = self.build_result_from_reply(reply, model, index=index, print_parsed=print_reply)
        return reply, result

    # -- public: build the per-column evidence the LLM reasons over ---------
    def build_columns_context(
        self, model: CanonicalAnalysisModel
    ) -> "tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]":
        """Return (context_rows, col_id -> {column, file_name, sheet_name}).

        Each context row is a compact JSON-serialisable description of one input
        column: a stable ``col_id``, the owning file/sheet, the zero-based
        position, the (possibly placeholder) name, and a bounded sample of
        non-null values. No decisions are made here.
        """
        rows: list[dict[str, Any]] = []
        index: dict[int, dict[str, Any]] = {}
        col_id = 0
        for file in self._input_files(model):
            for sheet in file.sheets:
                for column in sorted(sheet.columns, key=lambda c: c.position):
                    index[col_id] = {
                        "column": column,
                        "file_name": file.name,
                        "sheet_name": sheet.name,
                    }
                    rows.append(
                        {
                            "col_id": col_id,
                            "uuid": column.uuid,
                            "file": file.name,
                            "sheet": sheet.name,
                            "position": column.position,
                            "name": column.name,
                            "detected_type": column.data_type.value,
                            "samples": self._samples(column),
                        }
                    )
                    col_id += 1
        return rows, index

    # -- public: adapt an LLM reply into a ValidationAnalysisResult ---------
    def build_result_from_reply(
        self,
        reply: str,
        model: CanonicalAnalysisModel,
        *,
        index: Optional[dict[int, dict[str, Any]]] = None,
        one_row_per_column: bool = True,
        print_parsed: bool = False,
    ) -> ValidationAnalysisResult:
        """Translate the LLM's JSON decisions into a ValidationAnalysisResult.

        The result is structurally identical to the deterministic analyzer's
        output, so Deliverable 4 consumes it unchanged.

        Coverage (``one_row_per_column=True``, the default): EVERY input column
        becomes a profile, defaulting to a bare ``Text`` rule, with the LLM's
        detection applied on top when it returned that column. This matches the
        reference config, which emits exactly one ``validation-rules`` row per
        column (most of them plain ``Text``). When ``False``, only columns the
        LLM explicitly returned become profiles (legacy behaviour).
        """
        if index is None:
            _, index = self.build_columns_context(model)

        # Deterministic base: derive dataType / mandatory / length / uniqueness
        # for EVERY column straight from the canonical statistics. This is the
        # bulk of the reference validations and needs no LLM.
        base_result = self._analyzer.analyze(model)
        base_by_uuid: dict[str, ColumnValidationProfile] = {
            p.source_column_uuid: p for p in base_result.inventory.columns
        }

        payload = self._extract_json(reply)
        detections = payload.get("columns")
        if not isinstance(detections, list):
            raise LLMValidationDetectionError(
                "LLM reply JSON is missing a 'columns' array."
            )

        if print_parsed:
            # Manual step of validation: show the PARSED JSON the LLM returned
            # (post-extraction) so we can see what detections we are getting.
            print("=" * 78)
            print("[llm-validation] PARSED DETECTION JSON (from LLM reply)")
            print("=" * 78)
            print(f"columns detected by LLM: {len(detections)}")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            print("=" * 78)

        # Index the LLM detections by col_id (ignore malformed entries).
        det_by_id: dict[int, dict[str, Any]] = {}
        for det in detections:
            if isinstance(det, dict) and isinstance(det.get("col_id"), int):
                det_by_id[det["col_id"]] = det

        profiles: list[ColumnValidationProfile] = []
        if one_row_per_column:
            # One profile per column: deterministic base + LLM elevation on top.
            for col_id, entry in index.items():
                base = base_by_uuid.get(entry["column"].uuid)
                profile = self._build_profile(col_id, entry, det_by_id.get(col_id), base)
                if profile is not None and profile.requirements:
                    profiles.append(profile)
        else:
            # Legacy: only columns the LLM returned.
            for col_id, det in det_by_id.items():
                if col_id in index:
                    entry = index[col_id]
                    base = base_by_uuid.get(entry["column"].uuid)
                    profile = self._build_profile(col_id, entry, det, base)
                    if profile is not None and profile.requirements:
                        profiles.append(profile)

        inventory = ValidationRuleInventory(columns=profiles)
        expected_structures = self._expected_structures(model)
        summary = self._summarize(model, profiles)

        return ValidationAnalysisResult(
            session_id=model.session_id,
            expected_structures=expected_structures,
            inventory=inventory,
            unsupported=[],
            summary=summary,
        )

    # -- per-column translation --------------------------------------------
    def _build_profile(
        self,
        col_id: int,
        entry: dict[str, Any],
        det: Optional[dict[str, Any]],
        base: Optional[ColumnValidationProfile] = None,
    ) -> Optional[ColumnValidationProfile]:
        """Merge the deterministic base with the LLM overlay for one column.

        Precedence:

        * **dataType** - the deterministic type (from the actual values) wins,
          unless the LLM ELEVATES it to a more specific supported type
          (``Dropdown``/``Date``/``Email``/``Regex``). A ``None`` base type
          falls back to ``Text`` (the reference never leaves ``dataType`` null).
        * **mandatory / min-length / max-length / unique** - taken from the
          deterministic base (computed from null-counts and value lengths); the
          LLM may additionally flag ``mandatory``.
        * **named LOV / disallowed chars / date-format / regex** - added from
          the LLM overlay only (statistics cannot name a catalogue or a mask).

        Deliberately dropped from the base to match the reference's sparsity:
        ``minValue``/``maxValue``, bare ``numeric_format`` (no mask), and
        nameless ``list_of_values`` (the reference LOV always has a catalogue
        name, which only the LLM/domain knowledge can supply).
        """
        column: CanonicalColumn = entry["column"]
        det = det if isinstance(det, dict) else None

        # --- deterministic base facts -------------------------------------
        base_type: Optional[ContractDataType] = base.data_type if base else None
        base_mandatory = bool(base.is_mandatory) if base else False
        base_by_kind: dict[SupportedValidation, ValidationRequirement] = {}
        if base is not None:
            for r in base.requirements:
                base_by_kind.setdefault(r.kind, r)

        # --- resolve dataType: Text is the DEFAULT unless the type can be
        # positively figured out from the input-file data. Precedence:
        #   LLM decision (any supported type) > unambiguous format detection
        #   (Date / Email) > Text.
        # A bare numeric / None deterministic inference is NOT trusted to
        # override Text: numeric-looking values are frequently codes / IDs, so
        # when the type cannot be confidently determined the default stays Text.
        contract_type = ContractDataType.TEXT
        reason = "default Text (type not determinable from input data)"

        # Unambiguous, format-driven deterministic types may stand in for Text.
        if base_type in (ContractDataType.DATE, ContractDataType.EMAIL):
            contract_type = base_type
            reason = "deterministic format detection"

        # The LLM's explicit decision (when present) is authoritative - it is the
        # domain-knowledge layer that CAN figure the type out from the data.
        if det is not None:
            llm_type = self._coerce_data_type(det.get("dataType"))
            if llm_type is not None:
                contract_type = llm_type
                reason = str(det.get("reason") or "LLM decision")

        profile = ColumnValidationProfile(
            source_column_uuid=column.uuid,
            column_name=column.name,
            position=column.position,
            file_name=entry.get("file_name", ""),
            sheet_name=entry.get("sheet_name", ""),
            data_type=contract_type,
            is_mandatory=base_mandatory,
            is_nullable=not base_mandatory,
        )

        reqs: list[ValidationRequirement] = []

        # Anchor the dataType (compiler resolves the row's dataType from this).
        reqs.append(self._make_req(
            SupportedValidation.DATA_TYPE,
            parameters={"dataType": contract_type.value},
            rationale=reason,
        ))

        # --- carry the high-value deterministic constraints ---------------
        if SupportedValidation.MANDATORY in base_by_kind:
            reqs.append(base_by_kind[SupportedValidation.MANDATORY])
        # Length bounds only make sense for text-like types.
        if contract_type in (ContractDataType.TEXT, ContractDataType.REGEX):
            for kind in (SupportedValidation.MAX_LENGTH, SupportedValidation.MIN_LENGTH):
                if kind in base_by_kind:
                    reqs.append(base_by_kind[kind])
        if SupportedValidation.UNIQUE in base_by_kind:
            reqs.append(base_by_kind[SupportedValidation.UNIQUE])
        # A deterministic format mask (e.g. a date mask) is worth keeping.
        for kind in (SupportedValidation.DATE_FORMAT, SupportedValidation.REGEX_PATTERN):
            req = base_by_kind.get(kind)
            if req is not None and req.parameters.get("dataFormat"):
                reqs.append(req)

        # --- LLM overlay: domain knowledge the statistics cannot supply ---
        raw_validations = (det or {}).get("validations") or []
        seen_kinds = {r.kind for r in reqs}
        if isinstance(raw_validations, list):
            for v in raw_validations:
                req = self._map_validation(v, contract_type, reason)
                if req is None:
                    continue
                # Only overlay the domain-specific kinds; never let the LLM
                # re-add length/value bounds the deterministic base owns.
                if req.kind not in _LLM_OVERLAY_KINDS:
                    continue
                if req.kind in seen_kinds:
                    continue
                reqs.append(req)
                seen_kinds.add(req.kind)
                if req.kind is SupportedValidation.MANDATORY:
                    profile.is_mandatory = True
                    profile.is_nullable = False

        profile.requirements = reqs
        return profile

    def _map_validation(
        self,
        v: Any,
        contract_type: Optional[ContractDataType],
        reason: str,
    ) -> Optional[ValidationRequirement]:
        """Translate one LLM validation entry into a ValidationRequirement."""
        if not isinstance(v, dict):
            return None
        kind_token = str(v.get("kind", "")).strip().lower()
        kind = _KIND_TOKENS.get(kind_token)
        if kind is None:
            logger.warning("[llm-validation] unknown validation kind %r (skipped)", kind_token)
            return None

        params = self._params_for(kind, v, contract_type)
        if params is None:  # required parameter missing -> skip rather than invent
            logger.warning("[llm-validation] '%s' missing required params (skipped)", kind_token)
            return None

        return self._make_req(kind, parameters=params, rationale=f"LLM: {reason}")

    @staticmethod
    def _params_for(
        kind: SupportedValidation,
        v: dict[str, Any],
        contract_type: Optional[ContractDataType],
    ) -> Optional[dict[str, Any]]:
        """Shape the IR parameters for a kind from the LLM entry (no invention).

        Tolerant of the alternate key spellings LLMs commonly emit (e.g.
        ``catalogue``/``lov`` for the LOV name, ``regex``/``pattern`` for a
        regex, ``format`` for a mask) so a correct decision is never dropped on
        a key-name technicality.
        """
        def first(*keys: str) -> Optional[Any]:
            for k in keys:
                if v.get(k) not in (None, ""):
                    return v.get(k)
            return None

        if kind is SupportedValidation.MANDATORY:
            return {"isMandatory": "Y"}
        if kind is SupportedValidation.UNIQUE:
            return {"uniqueValueInColumn": "Y"}
        if kind is SupportedValidation.REQUIRED_IN_ERROR_FILE:
            return {"columnRequiredInErrorFile": "Y"}
        if kind is SupportedValidation.EMAIL_FORMAT:
            return {"dataType": ContractDataType.EMAIL.value}
        if kind is SupportedValidation.DATE_FORMAT:
            fmt = first("dataFormat", "format", "mask", "date_format")
            if not fmt:
                return None
            return {"dataType": ContractDataType.DATE.value, "dataFormat": str(fmt)}
        if kind is SupportedValidation.NUMERIC_FORMAT:
            out = {"dataType": ContractDataType.NUMBER.value}
            fmt = first("dataFormat", "format", "numeric_format")
            if fmt:
                out["dataFormat"] = str(fmt)
            return out
        if kind is SupportedValidation.REGEX_PATTERN:
            fmt = first("dataFormat", "regex", "pattern", "regex_pattern")
            if not fmt:
                return None
            return {"dataType": ContractDataType.REGEX.value, "dataFormat": str(fmt)}
        if kind is SupportedValidation.LIST_OF_VALUES:
            out: dict[str, Any] = {"lovValidationRequired": "Y"}
            name = first("lovCheckType", "catalogue", "catalog", "lov", "list", "name")
            if name:
                out["lovCheckType"] = str(name)
            if str(first("transformationRequired", "transformation") or "").strip().upper() == "Y":
                out["transformationRequired"] = "Y"
            return out
        if kind is SupportedValidation.SPECIAL_CHAR_NOT_ALLOWED:
            payload = first("specialCharNotAllowed", "specialChars", "characters")
            if payload is None:
                return None
            return {"specialCharNotAllowed": payload if isinstance(payload, str) else json.dumps(payload)}
        if kind is SupportedValidation.MIN_LENGTH:
            val = first("minLengthAllowed", "minLength", "min_length")
            return {"minLengthAllowed": str(val)} if val is not None else None
        if kind is SupportedValidation.MAX_LENGTH:
            val = first("maxLengthAllowed", "maxLength", "max_length")
            return {"maxLengthAllowed": str(val)} if val is not None else None
        if kind is SupportedValidation.MIN_VALUE:
            val = first("minValue", "min_value", "min")
            return {"minValue": str(val)} if val is not None else None
        if kind is SupportedValidation.MAX_VALUE:
            val = first("maxValue", "max_value", "max")
            return {"maxValue": str(val)} if val is not None else None
        if kind is SupportedValidation.STRING_CHECK:
            payload = first("stringCheckRule", "stringCheck")
            return {"stringCheckRule": payload} if payload is not None else None
        if kind is SupportedValidation.CONDITIONAL:
            payload = first("conditionalValidationRule", "conditional")
            return {"conditionalValidationRule": payload} if payload is not None else None
        return None

    @staticmethod
    def _make_req(
        kind: SupportedValidation,
        *,
        parameters: dict[str, Any],
        rationale: str,
    ) -> ValidationRequirement:
        """Build a SUPPORTED requirement wired to its catalogue contract field."""
        spec = get_spec(kind)
        return ValidationRequirement(
            kind=kind,
            status=SupportStatus.SUPPORTED,
            source=RequirementSource.LLM,
            contract_field=spec.contract_field,
            parameters=parameters,
            confidence=0.9,
            rationale=rationale,
        )

    # -- structure / summary (mirror the deterministic analyzer) -----------
    def _expected_structures(
        self, model: CanonicalAnalysisModel
    ) -> list[ExpectedStructure]:
        structures: list[ExpectedStructure] = []
        for file in self._input_files(model):
            for sheet in file.sheets:
                cols = [
                    ExpectedColumn(
                        name=c.name,
                        position=c.position,
                        source_column_uuid=c.uuid,
                    )
                    for c in sorted(sheet.columns, key=lambda c: c.position)
                ]
                structures.append(
                    ExpectedStructure(
                        file_name=file.name,
                        file_uuid=file.uuid,
                        sheet_name=sheet.name,
                        sheet_uuid=sheet.uuid,
                        expected_column_count=len(sheet.columns),
                        expected_columns=cols,
                    )
                )
        return structures

    def _summarize(
        self,
        model: CanonicalAnalysisModel,
        profiles: list[ColumnValidationProfile],
    ) -> AnalysisSummary:
        input_files = self._input_files(model)
        by_kind: dict[str, int] = {}
        supported = 0
        for profile in profiles:
            for req in profile.requirements:
                by_kind[req.kind.value] = by_kind.get(req.kind.value, 0) + 1
                supported += 1
        total_columns = sum(len(s.columns) for f in input_files for s in f.sheets)
        return AnalysisSummary(
            input_file_count=len(input_files),
            sheet_count=sum(len(f.sheets) for f in input_files),
            column_count=total_columns,
            requirement_count=supported,
            supported_count=supported,
            unsupported_count=0,
            mandatory_column_count=sum(1 for p in profiles if p.is_mandatory),
            nullable_column_count=sum(1 for p in profiles if p.is_nullable),
            requirements_by_kind=dict(sorted(by_kind.items())),
        )

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _input_files(model: CanonicalAnalysisModel) -> list[CanonicalFile]:
        return model.input_files or [
            f for f in model.all_files() if f.io_role is IoRole.INPUT
        ]

    @staticmethod
    def _samples(column: CanonicalColumn) -> list[str]:
        out: list[str] = []
        for v in column.sample_values:
            if v is None:
                continue
            s = str(v).strip()
            if s:
                out.append(s[:60])
            if len(out) >= _MAX_SAMPLES:
                break
        return out

    @staticmethod
    def _coerce_data_type(value: Any) -> Optional[ContractDataType]:
        if value is None:
            return None
        return _DATATYPE_TOKENS.get(str(value).strip().lower())

    @staticmethod
    def _extract_json(reply: str) -> dict[str, Any]:
        """Extract the JSON object from an LLM reply (tolerates fences/prose)."""
        if not reply or not reply.strip():
            raise LLMValidationDetectionError("LLM reply was empty.")
        text = reply.strip()
        # Strip ```json ... ``` fences if present.
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        if fence:
            text = fence.group(1).strip()
        # Fall back to the first {...} block.
        if not text.startswith("{"):
            brace = re.search(r"\{.*\}", text, re.DOTALL)
            if brace:
                text = brace.group(0)
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise LLMValidationDetectionError(f"LLM reply was not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise LLMValidationDetectionError("LLM reply JSON was not an object.")
        return data


__all__ = ["LLMValidationDetector", "LLMValidationDetectionError"]
