import re

with open('app/page.js', 'r') as f:
    text = f.read()

# Fix the duplicate export block. The block from `  const handleExportCSV = () => {` up to the second occurrence of it.
first_idx = text.find('  const handleExportCSV = () => {')
second_idx = text.find('  const handleExportCSV = () => {', first_idx + 10)

if second_idx != -1:
    text = text[:first_idx] + text[second_idx:]

# Fix the backslash before backticks
text = text.replace('return \\`', 'return `')
text = text.replace('\\`;', '`;')

with open('app/page.js', 'w') as f:
    f.write(text)
