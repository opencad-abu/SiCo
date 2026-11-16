"""Strict comparison of this fixed Spectre recipe, retaining subcircuit scope."""
import re


def canonical_netlist(text, dut='OpAmp'):
    text=re.sub(r'\\\s*\n', ' ', text)
    lines=[]
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith('//'):
            continue
        line=re.sub(r'\s+', ' ', line.strip())
        line=re.sub(r'\b'+re.escape(dut)+r'\b','OpAmp',line)
        line=re.sub(r'\bI_'+re.escape(dut)+r'_0\b','I0',line)
        line=re.sub(r'\bI_opamp_dc_feedback_0\b','FB',line)
        lines.append(line)
    blocks={}; scope='top'; devices=[]; directives=[]
    for line in lines:
        if line.startswith('subckt '):
            assert scope=='top'
            scope=line
            assert scope not in blocks
            blocks[scope]=[]
        elif line.startswith('ends '):
            assert scope!='top' and line.split()[1]==scope.split()[1]
            blocks[scope]=tuple(sorted(blocks[scope])); scope='top'
        elif scope!='top':
            blocks[scope].append(line)
        elif re.match(r'\w+ \(',line):
            devices.append(line)
        else:
            directives.append(line)
    assert scope=='top'
    return dict(subcircuits=blocks,devices=sorted(devices),directives=directives)
