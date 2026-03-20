import sys
with open("frontend/app/api/leads/route.js", "r") as f:
    text = f.read()

text = text.replace('if (county === "graham" || county === "la-paz" || county === "navajo") {', 'if (county === "graham" || county === "la-paz" || county === "navajo" || county === "santa-cruz") {')
text = text.replace('const tableName = county === "graham" ? "graham_leads" : (county === "la-paz" ? "lapaz_leads" : "navajo_leads");', 'const tableName = county === "graham" ? "graham_leads" : (county === "la-paz" ? "lapaz_leads" : (county === "navajo" ? "navajo_leads" : "santacruz_leads"));')

with open("frontend/app/api/leads/route.js", "w") as f:
    f.write(text)
