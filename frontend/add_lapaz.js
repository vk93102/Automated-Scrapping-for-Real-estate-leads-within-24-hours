const fs = require('fs');

// 1. Update backend route.js
let routeCode = fs.readFileSync('app/api/leads/route.js', 'utf8');

routeCode = routeCode.replace(
  'if (county === "graham") {',
  'if (county === "graham" || county === "la-paz") {\n      const tableName = county === "graham" ? "graham_leads" : "lapaz_leads";'
);
routeCode = routeCode.replace('FROM graham_leads', 'FROM ${tableName}');
routeCode = routeCode.replace('{ error: err?.message || "Failed to fetch Maricopa leads" }', '{ error: err?.message || "Failed to fetch leads" }');

fs.writeFileSync('app/api/leads/route.js', routeCode, 'utf8');

// 2. Update frontend page.js
let pageCode = fs.readFileSync('app/page.js', 'utf8');

pageCode = pageCode.replace(
  '{ key: "la-paz", name: "La Paz", status: "Planned" }',
  '{ key: "la-paz", name: "La Paz", status: "Live" }'
);

pageCode = pageCode.replace(
  'const isLiveCounty = activeCounty === "maricopa" || activeCounty === "graham";',
  'const isLiveCounty = ["maricopa", "graham", "la-paz"].includes(activeCounty);'
);

fs.writeFileSync('app/page.js', pageCode, 'utf8');
console.log('La Paz integrated successfully.');