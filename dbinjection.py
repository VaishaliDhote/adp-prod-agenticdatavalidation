"""
DB Injection Script
Inserts BU, Platform, and Category hierarchical data into DynamoDB.
"""
import sys
sys.path.insert(0, '.')

from storage.dynamodb.business_repository import (
    insert_bu,
    insert_platform,
    insert_category,
)

# Data structure: BU -> Platform -> [Categories]
data = {
    "1": {"2": ["3"]},
    "A": {"2": ["3"], "B": ["3", "C"]},
    "ADP BPM": {"ADP BPM": ["ADP BPM Test5"]},
    "ADP Connection Center": {"POC CSV": ["CSV Standard ADPC", "IMIG to DGE Kenzo", "Kenzo_Txt to IMIG"]},
    "ANZ IMP": {},
    "ANZ Services": {"Payforce": ["Variable Data"]},
    "Aakash Bu": {"Aakash Bu": ["Aakash Bu", "dsnTest", "v2"]},
    "AkashBuTest": {"TestValueValidation": ["ValueValid", "ValueValid-1", "ValueValid1"]},
    "Auto BU": {"Auto Platform": ["Auto Category"]},
    "BR SER": {"CELERGO": ["Termination Form"], "EXPERT": ["New Absence Record", "New Dependents", "Vacation"]},
    "BU TEST UI": {"ADP IE": ["New Hire"]},
    "Backend Testing BU": {"Backend Testing Platform": ["Backend Testing DC"]},
    "CAMEL": {"PECI_XML": ["TESTING", "newTest", "testing2"]},
    "CH SER": {"ABACUS": ["New Hire V2", "PayDeds Recurring Changes"]},
    "CN SER": {"PSS HK": ["Leavers Details"]},
    "CSL": {},
    "DE Training": {"Edlohn": ["Loga", "loga33"]},
    "DEEPANSHU": {"PECI": ["TestNew", "XMLTest"], "PeciXML": ["TEST", "TEST2"]},
    "DEEPANSHU SRIVASTAVA": {"PECI_XML": ["TEST"]},
    "DEEPANSHU TEST BU": {"ComplexLov": ["Testing", "Testing2", "testing3"], "Payroll": ["NewEmployee", "test"]},
    "DEEPANSHU_BU": {"PECI": ["Testing", "XML"]},
    "DS": {"PECI_XML": ["TEST"], "Peci": ["xml", "xml2", "xml3", "xml4"]},
    "DSBU": {"PECI": ["XML", "testing"]},
    "DSBUNew": {"Peci": ["ABC", "NewPeci", "NewPeci2", "Xml"]},
    "DSCAMEL": {"PECI_XML": ["TEST"]},
    "DSM": {},
    "ENV": {"ENV": ["ENV V1.0"]},
    "ES": {"ADP": ["ADP-DataTransform"]},
    "ESI": {"ADP": ["ADP-DataTransform"]},
    "GV": {"GIC": ["New Employee"]},
    "Germany": {},
    "IB IMP": {"EVO": ["EMPLEADOS", "Empresas"]},
    "INDTS": {"TS": ["GLD", "GLD2", "GLD3"]},
    "IT": {"OneService - ISS": ["New Hire"]},
    "Iberia": {},
    "India": {"IN-Vista": ["Getronics", "Incorp", "Poshmark"], "India Payroll Platform": ["New Hire"]},
    "Karan BU": {},
    "Kiran Test": {"CR Slippage": ["Sample Test"]},
    "Kiran Test BU": {"Training": ["EE New Hire", "Multisheet Excel"]},
    "Kiran_Test": {"ADP Connection Center": ["Test 16_Nested PECI"]},
    "MJ": {"ADP MJ": ["MJ!"]},
    "MNKtestBU": {},
    "NEWBU": {"Peci": ["Xml"]},
    "NL IMP": {"IHCM": ["Enviem", "Enviem 01", "Enviem 03", "Enviem 04", "Enviem 06"]},
    "PDF Test": {"PDF Test": ["PDF Test", "PDF Test1"]},
    "PECIBU": {"Peci": ["Xml", "Xml-personal"]},
    "PECIBU2": {"Peci": ["Xml", "XmlTest", "XmlTest2"]},
    "PL": {"PL IMPL": ["ADP PL2", "ADP PL2 NDF", "ADP PL3", "ADP PL4"]},
    "PL SER": {"RB PAYROLL": ["Single_Changes"]},
    "PROD-AWS-TESTING": {"PROD-AWS-TESTING": ["PROD-AWS-TESTING"]},
    "Rahul": {},
    "Rama BU": {"paycheck": ["payroll"]},
    "SA": {"POC": ["Excel Pivot"]},
    "SA SER": {"SAGE 200": ["Angola_NewHire"]},
    "Saurav Test BU": {"Saurav Test Platform": ["1 Tab", "Data Identifier test", "Multiple excel 1 sheet"]},
    "SharePoint": {"SharePointPlatform": ["SharePointDC"], "SharePointPlatform2": ["SharePointDC2"]},
    "Shiva": {"Shiva": ["Excel-2-Json", "Excel-TO-JSON"]},
    "TEST": {"ADP IEE": ["Personal Identification"]},
    "TEST POC UMESH": {"POC": ["Additional Personal Details", "Datev LODAS", "Tax Details", "payment & deduction"]},
    "TEST-KT-BU": {"TEST PLATFORM": ["KT DATA CATERORY", "KT DATA CATERORY - 1"]},
    "TEST_UMESH": {},
    "Talend": {"Talend": ["Paisy P1 Variable File"]},
    "Test_Sush": {"Test_Sush": ["data_category_test_sush", "data_category_test_sush1", "single_sheet1"]},
    "Test_Umesh": {},
    "Test_Umesh1": {"Test_Umesh1": ["Test51", "TestFelds2", "TestFields", "XML Hehe", "XML TEST", "txtfile"]},
    "TrainingBU": {"Product 01": ["Test Text File MultiHeader based"]},
    "UT BU": {"JUNE2026": ["ANSI", "ANSI2", "DATASET-EMPTY-AFTER-RULES-FILTER"]},
    "VV": {"IHCM": ["process1", "process2"], "Liffon": ["process1"]},
    "aakash": {"aakash": ["aakash"]},
    "aakashbu": {"Aakash": ["Aakash"]},
    "e": {"f": ["g"]},
    "g": {"h": ["i"]},
    "t": {"ADPHK": ["New Hire"]},
    "test": {"ADPDE": ["JOB DETAILS", "New Hire"], "ADPHK": ["JOB DETAILS"], "ADPIE": ["New Hire"]},
    "test1": {"ADPHK1": ["JOB DETAILS"], "ADPHK2": ["JOB DETAILS", "New Hire"]},
    "test2": {"ADPHK2": ["New Hire"], "ADPP": ["New Hire"]},
    "test162": {"ADP": ["New Hire ADP"]},
    "testing": {"test": ["test"], "testing": ["testing"]},
    "testing2": {"testing3": ["testing2", "testing4"]},
    "undefined": {"undefined": ["undefined"]},
    "x": {"y": ["z"]},
    "x1": {"y": ["z"], "y1": ["z"]},
    "x12": {"y12": ["z"]},
    "x122": {"y122": ["z1"]}
}


def inject_hierarchy(data: dict) -> dict:
    """
    Inject BU -> Platform -> Category hierarchy into DynamoDB.
    
    Args:
        data: Dict with structure {bu: {platform: [categories]}}
    
    Returns:
        Summary dict with counts of inserted vs skipped items
    """
    stats = {
        "bu": {"inserted": 0, "skipped": 0, "errors": []},
        "platform": {"inserted": 0, "skipped": 0, "errors": []},
        "category": {"inserted": 0, "skipped": 0, "errors": []}
    }
    
    for bu_name, platforms in data.items():
        # Insert BU
        try:
            insert_bu(bu_name)
            stats["bu"]["inserted"] += 1
            print(f"✓ Inserted BU: {bu_name}")
        except ValueError as e:
            if "already exists" in str(e):
                stats["bu"]["skipped"] += 1
                print(f"⊘ Skipped BU (exists): {bu_name}")
            else:
                stats["bu"]["errors"].append(f"{bu_name}: {e}")
                print(f"✗ Error inserting BU '{bu_name}': {e}")
                continue  # Skip platforms if BU failed
        
        # Insert Platforms under this BU
        for platform_name, categories in platforms.items():
            try:
                insert_platform(bu_name, platform_name)
                stats["platform"]["inserted"] += 1
                print(f"  ✓ Inserted Platform: {platform_name} under BU: {bu_name}")
            except ValueError as e:
                if "already exists" in str(e):
                    stats["platform"]["skipped"] += 1
                    print(f"  ⊘ Skipped Platform (exists): {platform_name}")
                else:
                    stats["platform"]["errors"].append(f"{bu_name}/{platform_name}: {e}")
                    print(f"  ✗ Error inserting Platform '{platform_name}': {e}")
                    continue  # Skip categories if Platform failed
            
            # Insert Categories under this Platform
            for category_name in categories:
                try:
                    insert_category(bu_name, platform_name, category_name)
                    stats["category"]["inserted"] += 1
                    print(f"    ✓ Inserted Category: {category_name}")
                except ValueError as e:
                    if "already exists" in str(e):
                        stats["category"]["skipped"] += 1
                        print(f"    ⊘ Skipped Category (exists): {category_name}")
                    else:
                        stats["category"]["errors"].append(f"{bu_name}/{platform_name}/{category_name}: {e}")
                        print(f"    ✗ Error inserting Category '{category_name}': {e}")
    
    return stats


if __name__ == "__main__":
    # Display the data structure
    print("Data to be inserted:")
    for bu, platforms in data.items():
        print(f"  BU: {bu}")
        for platform, categories in platforms.items():
            print(f"    Platform: {platform}")
            print(f"      Categories: {categories}")
    
    # Run the injection
    print("\n" + "=" * 60)
    print("Starting data injection...")
    print("=" * 60)

    stats = inject_hierarchy(data)

    # Print summary
    print("\n" + "=" * 60)
    print("INJECTION SUMMARY")
    print("=" * 60)
    print(f"Business Units:  {stats['bu']['inserted']} inserted, {stats['bu']['skipped']} skipped")
    print(f"Platforms:       {stats['platform']['inserted']} inserted, {stats['platform']['skipped']} skipped")
    print(f"Categories:      {stats['category']['inserted']} inserted, {stats['category']['skipped']} skipped")

    if any(stats[k]["errors"] for k in stats):
        print("\nErrors:")
        for entity_type, entity_stats in stats.items():
            for error in entity_stats["errors"]:
                print(f"  - {entity_type.upper()}: {error}")
    else:
        print("\nNo errors encountered!")
