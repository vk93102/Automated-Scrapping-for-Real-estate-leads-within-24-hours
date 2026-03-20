import sys
with open("app/api/leads/route.js", "r") as f:
    text = f.read()

old_if = 'if (county === "graham" || county === "la-paz" || county === "navajo" || county === "santa-cruz") {'
new_if = 'if (["graham", "la-paz", "navajo", "santa-cruz", "greenlee", "cochise"].includes(county)) {'
text = text.replace(old_if, new_if)

old_assignment = 'const tableName = county === "graham" ? "graham_leads" : (county === "la-paz" ? "lapaz_leads" : (county === "navajo" ? "navajo_leads" : "santacruz_leads"));'
new_assignment = '''const tableMap = {
        "graham": "graham_leads",
        "la-paz": "lapaz_leads",
        "navajo": "navajo_leads",
        "santa-cruz": "santacruz_leads",
        "greenlee": "greenlee_leads",
        "cochise": "cochise_leads"
      };
      const tableName = tableMap[county];'''

text = text.replace(old_assignment, new_assignment)

with open("app/api/leads/route.js", "w") as f:
    f.write(text)
