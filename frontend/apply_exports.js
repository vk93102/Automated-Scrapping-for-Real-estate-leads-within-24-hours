const fs = require('fs');

let content = fs.readFileSync('app/page.js', 'utf8');

// Update brand
content = content.replace('<span className="brand-text">LeadOps Pro</span>', '<span className="brand-text">Arizona Foreclosure DB</span>');

// Remove Admin user completely and place a database generic icon profile
content = content.replace(/<div className="user-profile">[\s\S]*?<\/div>/, `<div className="user-profile"><Icon path={ICONS.database} /></div>`);

// Add export icons
if (!content.includes('download:')) {
  content = content.replace('check: "M20 6L9 17l-5-5",', 'check: "M20 6L9 17l-5-5",\n  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4 M7 10l5 5 5-5 M12 15V3",\n  printer: "M6 9V2h12v7M6 18H4a2 2 0 01-2-2v-5a2 2 0 012-2h16a2 2 0 012 2v5a2 2 0 01-2 2h-2M6 14h12v8H6z",');
}

const exportFns = `
  const handleExportCSV = () => {
    if (rows.length === 0) return;
    const displayRows = rows.filter(r => {
      if (addressFilter === "with") return r.property_address && r.property_address.trim() !== "";
      if (addressFilter === "without") return !r.property_address || r.property_address.trim() === "";
      return true;
    });

    const headers = ["Recording Number", "Type", "Recorded Date", "Borrower/Trustor", "Address", "City", "Principal Balance", "System Date"];
    const csvRows = displayRows.map(row => {
      const doc = row.documents || {};
      return [
        \`"\${doc.recording_number || ""}"\`,
        \`"\${doc.document_type || ""}"\`,
        \`"\${doc.recording_date || ""}"\`,
        \`"\${(row.trustor_1_full_name || row.trustor_2_full_name || "").replace(/"/g, '""')}"\`,
        \`"\${(row.property_address || "").replace(/"/g, '""')}"\`,
        \`"\${row.address_city || ""}"\`,
        \`"\${row.original_principal_balance || ""}"\`,
        \`"\${formatDate(row.created_at)}"\`
      ].join(",");
    });

    const csvContent = [headers.join(","), ...csvRows].join("\\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", \`\${activeCounty}_leads_\${new Date().toISOString().slice(0,10)}.csv\`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleExportPDF = () => {
    if (rows.length === 0) return;
    const displayRows = rows.filter(r => {
      if (addressFilter === "with") return r.property_address && r.property_address.trim() !== "";
      if (addressFilter === "without") return !r.property_address || r.property_address.trim() === "";
      return true;
    });

    const printWindow = window.open("", "_blank");
    printWindow.document.write(\`
      <html>
        <head>
          <title>\${activeCounty.toUpperCase()} Leads - Export</title>
          <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; padding: 20px; font-size: 12px; }
            table { width: 100%; border-collapse: collapse; margin-top: 20px; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f8f9fa; font-weight: bold; }
            h2 { margin-top: 0; color: #111827; }
            .print-btn { display: block; margin-bottom: 20px; background: #000; color: #fff; border: 0; padding: 8px 16px; border-radius: 4px; cursor: pointer; }
            @media print { .print-btn { display: none; } }
          </style>
        </head>
        <body>
          <button class="print-btn" onclick="window.print()">Print / Save as PDF</button>
          <h2>\${activeCounty.toUpperCase()} County - Foreclosure Records</h2>
          <p>Generated on: \${new Date().toLocaleString()}</p>
          <table>
            <thead>
              <tr>
                <th>Recording</th>
                <th>Type</th>
                <th>Recorded Date</th>
                <th>Borrower / Trustor</th>
                <th>Address</th>
                <th>Principal Bal</th>
              </tr>
            </thead>
            <tbody>
              \${displayRows.map(row => {
                const doc = row.documents || {};
                return \\\`
                  <tr>
                    <td>\${doc.recording_number || "-"}</td>
                    <td>\${doc.document_type || "-"}</td>
                    <td>\${doc.recording_date || "-"}</td>
                    <td>\${row.trustor_1_full_name || row.trustor_2_full_name || "-"}</td>
                    <td>\${row.property_address || "-"} \${row.address_city || ""}</td>
                    <td>\${row.original_principal_balance || "-"}</td>
                  </tr>
                \\\`;
              }).join("")}
            </tbody>
          </table>
        </body>
      </html>
    \`);
    printWindow.document.close();
    printWindow.focus();
  };

  const isLiveCounty = activeCounty === "maricopa" || activeCounty === "graham";
`;

content = content.replace('const isLiveCounty = activeCounty === "maricopa" || activeCounty === "graham";', exportFns);

// Update export buttons
const exportBtns = `                  <div className="filter-group">
                    <button className="filter-btn" onClick={handleExportCSV} disabled={rows.length===0} title="Download CSV" style={{border: '1px solid currentColor', background:'transparent'}}>
                      <Icon path={ICONS.download} style={{marginRight: '6px'}}/> CSV
                    </button>
                    <button className="filter-btn" onClick={handleExportPDF} disabled={rows.length===0} title="Print / PDF" style={{border: '1px solid currentColor', background:'transparent'}}>
                      <Icon path={ICONS.printer} style={{marginRight: '6px'}}/> PDF
                    </button>
                  </div>`;

if(content.includes('<button className="action-btn">Export CSV</button>')){
  content = content.replace('<button className="action-btn">Export CSV</button>', exportBtns);
}

fs.writeFileSync('app/page.js', content, 'utf8');
console.log('Update finished.');