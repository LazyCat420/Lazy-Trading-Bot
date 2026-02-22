import xml.etree.ElementTree as ET

tree = ET.parse('results.xml')
root = tree.getroot()

print("=== FAILED TESTS ===")
count = 0
for testsuite in root.findall('.//testsuite'):
    for testcase in testsuite.findall('testcase'):
        failure = testcase.find('failure')
        if failure is not None:
            count += 1
            print(f"[{count}] {testcase.get('classname')}.{testcase.get('name')}")
