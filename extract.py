import sys, re

with open('backend/pragon_ui.py', 'r', encoding='utf-8') as f:
    content = f.read()

def extract(var_name, out_file):
    global content
    m = re.search(var_name + r'\s*=\s*r\"\"\"(.*?)\"\"\"', content, re.DOTALL)
    if m:
        with open(out_file, 'w', encoding='utf-8') as out:
            out.write(m.group(1))
        content = content[:m.start()] + var_name + ' = \"\"' + content[m.end():]
        print(f'Extracted {var_name} to {out_file}')
    else:
        print(f'Failed to extract {var_name}')

extract('PRAGON_HTML', 'frontend/index.html')
extract('PRAGON_COMPASS_HTML', 'frontend/compass.html')
extract('PRAGON_PULSE_HTML', 'frontend/pulse.html')

with open('backend/pragon_ui.py', 'w', encoding='utf-8') as f:
    f.write(content)
print('Finished extracting and removing strings from pragon_ui.py')
