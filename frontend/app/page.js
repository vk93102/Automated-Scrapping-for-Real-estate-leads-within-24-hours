"use client";

import { useEffect, useMemo, useState } from "react";

const FILTERS = [
  { label: "Last Day", value: "day" },
  { label: "Last Week", value: "week" },
  { label: "Last Month", value: "month" },
  { label: "End to End", value: "all" },
];

const COUNTIES = [
  { key: "maricopa", name: "Maricopa", status: "Live" },
  { key: "gila", name: "Gila", status: "Planned" },
  { key: "graham", name: "Graham", status: "Live" },
  { key: "greenlee", name: "Greenlee", status: "Live" },
  { key: "navajo", name: "Navajo", status: "Live" },
  { key: "cochise", name: "Cochise", status: "Live" },
  { key: "la-paz", name: "La Paz", status: "Live" },
  { key: "coconino", name: "Coconino", status: "Planned" },
  { key: "santa-cruz", name: "Santa Cruz", status: "Live" },
];

const DOC_TYPES = [
  { name: "Notice of Trustee Sale", desc: "Foreclosure initiation notice due to mortgage default." },
  { name: "Lis Pendens", desc: "Formal notice of a pending lawsuit involving the property." },
  { name: "Deed in Lieu", desc: "Voluntary property title transfer to lender to avoid foreclosure." },
  { name: "Notice of Reinstatement", desc: "Filed when a defaulted loan is successfully brought current." },
  { name: "Treasurer's Deed", desc: "Transfers ownership after a tax lien sale." },
];

function formatDate(value) {
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return String(value);
  return dt.toLocaleString();
}

function formatShortDate(value) {
  if (!value) return "-";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return String(value);
  return dt.toLocaleDateString();
}

function Icon({ path, className = "" }) {
  return (
    <svg className={className} width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d={path} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

const ICONS = {
  county: "M3 21h18M5 21V7l7-4 7 4v14M9 10h.01M15 10h.01M9 14h.01M15 14h.01",
  database: "M4 7c0-2.2 3.6-4 8-4s8 1.8 8 4-3.6 4-8 4-8-1.8-8-4Zm0 0v5c0 2.2 3.6 4 8 4s8-1.8 8-4v-5",
  status: "M12 22S7 15 7 10a5 5 0 0 1 10 0c0 5-5 12-5 12z M12 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  leads: "M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2 M9 7a4 4 0 1 0 0-8 4 4 0 0 0 0 8z",
  location: "M12 21s7-5.3 7-11a7 7 0 1 0-14 0c0 5.7 7 11 7 11Zm0-8a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z",
  calendar: "M19 4H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2z M16 2v4 M8 2v4 M3 10h18",
  menu: "M4 6h16M4 12h16M4 18h16",
  search: "m21 21-4.3-4.3M11 19a8 8 0 1 1 0-16 8 8 0 0 1 0 16Z",
  home: "M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z M9 22V12h6v10",
  settings: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z M12 16v-4 M12 8h.01",
  info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z M12 16v-4 M12 8h.01",
  check: "M20 6L9 17l-5-5",
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4 M7 10l5 5 5-5 M12 15V3",
  printer: "M6 9V2h12v7M6 18H4a2 2 0 01-2-2v-5a2 2 0 012-2h16a2 2 0 012 2v5a2 2 0 01-2 2h-2M6 14h12v8H6z",
};

export default function HomePage() {
  const [activeCounty, setActiveCounty] = useState("maricopa");
  const [range, setRange] = useState("all");
  const [addressFilter, setAddressFilter] = useState("all");
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [isSearching, setIsSearching] = useState(false);
  const [searchResults, setSearchResults] = useState([]);
  const isGrahamView = activeCounty === "graham";
  const isMaricopaView = activeCounty === "maricopa";

  
  const handleExportCSV = () => {
    const activeRows = searchQuery && searchQuery.length >= 2 ? searchResults : rows;
    if (activeRows.length === 0) return;
    const displayRows = activeRows.filter(r => {
      if (addressFilter === "with") return r.property_address && r.property_address.trim() !== "";
      if (addressFilter === "without") return !r.property_address || r.property_address.trim() === "";
      return true;
    });

    const headers = isGrahamView
      ? [
          "Recording Number",
          "Recorded Date",
          "Grantors",
          "Grantees",
          "Principal Amount",
          "Property Address"
        ]
      : isMaricopaView
      ? [
          "Trustor 1 Full Name",
          "Trustor 2 Full Name",
          "Property Address",
          "Address City",
          "Address State",
          "Recording Number",
          "Recording Date",
          "Principal Amount"
        ]
      : [
          "Recording Number",
          "Type",
          "Recorded Date",
          "Borrower/Trustor",
          "Address",
          "City",
          "Principal Balance",
          "System Date"
        ];
    const csvRows = displayRows.map(row => {
      const doc = row.documents || {};
      if (isGrahamView) {
        return [
          `"${doc.recording_number || ""}"`,
          `"${doc.recording_date || ""}"`,
          `"${(row.grantors || "").replace(/"/g, '""')}"`,
          `"${(row.grantees || "").replace(/"/g, '""')}"`,
          `"${row.original_principal_balance || row.principal_amount || ""}"`,
          `"${(row.property_address || "").replace(/"/g, '""')}"`
        ].join(",");
      }
      if (isMaricopaView) {
        return [
          `"${(row.trustor_1_full_name || "").replace(/"/g, '""')}"`,
          `"${(row.trustor_2_full_name || "").replace(/"/g, '""')}"`,
          `"${(row.property_address || "").replace(/"/g, '""')}"`,
          `"${(row.address_city || "").replace(/"/g, '""')}"`,
          `"${(row.address_state || "").replace(/"/g, '""')}"`,
          `"${doc.recording_number || ""}"`,
          `"${doc.recording_date || ""}"`,
          `"${row.original_principal_balance || ""}"`
        ].join(",");
      }
      return [
        `"${doc.recording_number || ""}"`,
        `"${doc.document_type || ""}"`,
        `"${doc.recording_date || ""}"`,
        `"${(row.trustor_1_full_name || row.trustor_2_full_name || "").replace(/"/g, '""')}"`,
        `"${(row.property_address || "").replace(/"/g, '""')}"`,
        `"${row.address_city || ""}"`,
        `"${row.original_principal_balance || ""}"`,
        `"${formatDate(row.created_at)}"`
      ].join(",");
    });

    const csvContent = [headers.join(","), ...csvRows].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.setAttribute("href", url);
    link.setAttribute("download", `${activeCounty}_leads_${new Date().toISOString().slice(0,10)}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const handleExportPDF = () => {
    const activeRows = searchQuery && searchQuery.length >= 2 ? searchResults : rows;
    if (activeRows.length === 0) return;
    const displayRows = activeRows.filter(r => {
      if (addressFilter === "with") return r.property_address && r.property_address.trim() !== "";
      if (addressFilter === "without") return !r.property_address || r.property_address.trim() === "";
      return true;
    });

    const printWindow = window.open("", "_blank");
    printWindow.document.write(`
      <html>
        <head>
          <title>${activeCounty.toUpperCase()} Leads - Export</title>
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
          <h2>${activeCounty.toUpperCase()} County - Foreclosure Records</h2>
          <p>Generated on: ${new Date().toLocaleString()}</p>
          <table>
            <thead>
              <tr>
                ${isGrahamView
                  ? `
                    <th>Recording</th>
                    <th>Recorded Date</th>
                    <th>Grantors</th>
                    <th>Grantees</th>
                    <th>Principal Amount</th>
                    <th>Property Address</th>
                  `
                  : isMaricopaView
                  ? `
                    <th>Trustor 1 Full Name</th>
                    <th>Trustor 2 Full Name</th>
                    <th>Property Address</th>
                    <th>Address City</th>
                    <th>Address State</th>
                    <th>Recording Number</th>
                    <th>Recording Date</th>
                    <th>Principal Amount</th>
                  `
                  : `
                    <th>Recording</th>
                    <th>Type</th>
                    <th>Recorded Date</th>
                    <th>Borrower / Trustor</th>
                    <th>Address</th>
                    <th>Principal Bal</th>
                  `}
              </tr>
            </thead>
            <tbody>
              ${displayRows.map(row => {
                const doc = row.documents || {};
                if (isGrahamView) {
                  return `
                    <tr>
                      <td>${doc.recording_number || "-"}</td>
                      <td>${doc.recording_date || "-"}</td>
                      <td>${row.grantors || "-"}</td>
                      <td>${row.grantees || "-"}</td>
                      <td>${row.original_principal_balance || row.principal_amount || "-"}</td>
                      <td>${row.property_address || "-"}</td>
                    </tr>
                  `;
                }
                if (isMaricopaView) {
                  return `
                    <tr>
                      <td>${row.trustor_1_full_name || "-"}</td>
                      <td>${row.trustor_2_full_name || "-"}</td>
                      <td>${row.property_address || "-"}</td>
                      <td>${row.address_city || "-"}</td>
                      <td>${row.address_state || "-"}</td>
                      <td>${doc.recording_number || "-"}</td>
                      <td>${doc.recording_date || "-"}</td>
                      <td>${row.original_principal_balance || "-"}</td>
                    </tr>
                  `;
                }
                return `
                  <tr>
                    <td>${doc.recording_number || "-"}</td>
                    <td>${doc.document_type || "-"}</td>
                    <td>${doc.recording_date || "-"}</td>
                    <td>${row.trustor_1_full_name || row.trustor_2_full_name || "-"}</td>
                    <td>${row.property_address || "-"} ${row.address_city || ""}</td>
                    <td>${row.original_principal_balance || "-"}</td>
                  </tr>
                `;
              }).join("")}
            </tbody>
          </table>
        </body>
      </html>
    `);
    printWindow.document.close();
    printWindow.focus();
  };

  const isLiveCounty = ["maricopa", "graham", "la-paz", "navajo", "santa-cruz", "greenlee", "cochise"].includes(activeCounty);

  // Search functionality
  useEffect(() => {
    if (!searchQuery || searchQuery.length < 2) {
      setSearchResults([]);
      setIsSearching(false);
      return;
    }

    const abort = new AbortController();

    async function performSearch() {
      try {
        setIsSearching(true);
        const res = await fetch(
          `/api/search?q=${encodeURIComponent(searchQuery)}&county=${activeCounty}`,
          { signal: abort.signal, cache: "no-store" }
        );

        if (!res.ok) {
          throw new Error("Search failed");
        }

        const data = await res.json();
        setSearchResults(Array.isArray(data?.rows) ? data.rows : []);
      } catch (e) {
        if (e.name !== "AbortError") {
          setSearchResults([]);
        }
      } finally {
        setIsSearching(false);
      }
    }

    const timer = setTimeout(performSearch, 300);
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [searchQuery, activeCounty]);

  useEffect(() => {
    if (!isLiveCounty) {
      setRows([]);
      setError("");
      setLoading(false);
      return;
    }

    const abort = new AbortController();

    async function load() {
      try {
        setLoading(true);
        setError("");

        const res = await fetch(`/api/leads?range=${range}&county=${activeCounty}`, {
          signal: abort.signal,
          cache: "no-store",
        });

        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body?.error || "Failed to fetch leads");
        }

        const body = await res.json();
        setRows(Array.isArray(body?.rows) ? body.rows : []);
      } catch (e) {
        if (e.name !== "AbortError") {
          setError(e.message || "Unknown error");
        }
      } finally {
        setLoading(false);
      }
    }

    load();
    return () => abort.abort();
  }, [range, isLiveCounty, activeCounty]);

  const stats = useMemo(() => {
    const withAddress = rows.filter((r) => r.property_address).length;
    const latestLeadDate = rows.length > 0 ? rows[0]?.created_at : null;
    return {
      total: rows.length,
      withAddress,
      latestLeadDate,
    };
  }, [rows]);

  return (
    <div className="dashboard-shell">
      {/* Sidebar Navigation */}
      <aside className={`sidebar ${mobileMenuOpen ? "open" : ""}`}>
        <div className="sidebar-header">
          <div className="brand">
            <div className="brand-logo"></div>
            <span className="brand-text">Arizona Foreclosure DB</span>
          </div>
          <button className="mobile-close" onClick={() => setMobileMenuOpen(false)}>×</button>
        </div>

        <div className="sidebar-section">
          <h3 className="section-label">Arizona Counties</h3>
          <nav className="nav-menu">
            {COUNTIES.map((county) => {
              const active = county.key === activeCounty;
              return (
                <button
                  key={county.key}
                  className={`nav-item ${active ? "active" : ""} ${county.status !== "Live" ? "disabled" : ""}`}
                  onClick={() => {
                    setActiveCounty(county.key);
                    setMobileMenuOpen(false);
                  }}
                >
                  <span className="nav-item-icon">
                    <Icon path={ICONS.county} />
                  </span>
                  <span className="nav-item-text">{county.name}</span>
                  {county.status === "Live" ? (
                    <span className="badge badge-success">Live</span>
                  ) : (
                    <span className="badge badge-neutral">Plan</span>
                  )}
                </button>
              );
            })}
          </nav>
        </div>
      </aside>

      {/* Main Content Area */}
      <main className="main-content">
        {/* Top Header */}
                        <header className="top-header">
          <div className="header-left">
            <button className="mobile-menu-btn" onClick={() => setMobileMenuOpen(true)}>
              <Icon path={ICONS.menu} />
            </button>
            <h1 className="page-title">{COUNTIES.find((c) => c.key === activeCounty)?.name} Real Estate Leads</h1>
          </div>
          <div className="user-profile">
            <Icon path={ICONS.database} />
          </div>
        </header>

        <div className="content-wrapper">
          {/* Top Metrics Cards */}
          <div className="metrics-grid">
            <div className="metric-card">
              <div className="metric-icon-wrap bg-blue"><Icon path={ICONS.leads} /></div>
              <div className="metric-data">
                <span className="metric-label">Total Leads</span>
                <strong className="metric-value">{isLiveCounty ? stats.total : "-"}</strong>
              </div>
            </div>
            <div className="metric-card">
              <div className="metric-icon-wrap bg-green"><Icon path={ICONS.location} /></div>
              <div className="metric-data">
                <span className="metric-label">With Addresses</span>
                <strong className="metric-value">{isLiveCounty ? stats.withAddress : "-"}</strong>
              </div>
            </div>
            <div className="metric-card">
              <div className="metric-icon-wrap bg-purple"><Icon path={ICONS.status} /></div>
              <div className="metric-data">
                <span className="metric-label">County Status</span>
                <strong className="metric-value">{isLiveCounty ? "Active Now" : "Planned"}</strong>
              </div>
            </div>
            <div className="metric-card">
              <div className="metric-icon-wrap bg-orange"><Icon path={ICONS.calendar} /></div>
              <div className="metric-data">
                <span className="metric-label">Latest Record</span>
                <strong className="metric-value">{isLiveCounty ? formatShortDate(stats.latestLeadDate) : "-"}</strong>
              </div>
            </div>
          </div>

          <div className="content-panel">
            {/* Document Types Info Panel */}
            <div className="info-banner">
              <div className="banner-header">
                <Icon path={ICONS.info} className="info-icon" />
                <h4>Understanding Document Types</h4>
              </div>
              <div className="doc-definitions">
                {DOC_TYPES.map(dt => (
                  <div key={dt.name} className="doc-def">
                    <span className="dt-name">{dt.name}:</span>
                    <span className="dt-desc">{dt.desc}</span>
                  </div>
                ))}
              </div>
            </div>

            {isLiveCounty ? (
              <>
                <div className="data-toolbar">
                  <div className="filter-group">
                    {FILTERS.map((f) => (
                      <button
                        key={f.value}
                        type="button"
                        className={`filter-btn ${range === f.value ? "active" : ""}`}
                        onClick={() => setRange(f.value)}
                      >
                        {f.label}
                      </button>
                    ))}
                  </div>

                  <div className="search-box" style={{ flex: 1, maxWidth: "300px" }}>
                    <div style={{ position: "relative" }}>
                      <Icon path={ICONS.search} style={{ position: "absolute", left: "10px", top: "50%", transform: "translateY(-50%)", color: "#666", pointerEvents: "none" }} />
                      <input
                        type="text"
                        placeholder="Search by name, address, recording ID..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        style={{
                          width: "100%",
                          padding: "8px 10px 8px 36px",
                          border: "1px solid #ddd",
                          borderRadius: "4px",
                          fontSize: "14px",
                          boxSizing: "border-box",
                        }}
                      />
                    </div>
                  </div>

                  <div className="filter-group">
                    <button className={`filter-btn ${addressFilter === "all" ? "active" : ""}`} onClick={() => setAddressFilter("all")}>All Records</button>
                    <button className={`filter-btn ${addressFilter === "with" ? "active" : ""}`} onClick={() => setAddressFilter("with")}>Has Address</button>
                    <button className={`filter-btn ${addressFilter === "without" ? "active" : ""}`} onClick={() => setAddressFilter("without")}>No Address</button>
                  </div>

                                    <div className="filter-group">
                    <button className="filter-btn" onClick={handleExportCSV} disabled={rows.length===0} title="Download CSV" style={{border: '1px solid currentColor', background:'transparent'}}>
                      <Icon path={ICONS.download} style={{marginRight: '6px'}}/> CSV
                    </button>
                    <button className="filter-btn" onClick={handleExportPDF} disabled={rows.length===0} title="Print / PDF" style={{border: '1px solid currentColor', background:'transparent'}}>
                      <Icon path={ICONS.printer} style={{marginRight: '6px'}}/> PDF
                    </button>
                  </div>
                </div>

                {error && (
                  <div className="alert-error">
                    <Icon path={ICONS.info} /> {error}
                  </div>
                )}

                <div className="table-container">
                  {(loading || isSearching) && (
                    <div className="loading-state">
                      <div className="spinner"></div>
                      {isSearching ? "Searching..." : "Processing latest database records..."}
                    </div>
                  )}
                  
                  {!loading && !isSearching && (
                    <table className="data-table">
                      <thead>
                        <tr>
                          {isGrahamView ? (
                            <>
                              <th>Recording</th>
                              <th>Recorded Date</th>
                              <th>Grantors</th>
                              <th>Grantees</th>
                              <th>Principal Amount</th>
                              <th>Property Address</th>
                            </>
                          ) : isMaricopaView ? (
                            <>
                              <th>Trustor 1 Full Name</th>
                              <th>Trustor 2 Full Name</th>
                              <th>Property Address</th>
                              <th>Address City</th>
                              <th>Address State</th>
                              <th>Recording Number</th>
                              <th>Recording Date</th>
                              <th>Principal Amount</th>
                            </>
                          ) : (
                            <>
                              <th>Recording</th>
                              <th>Type</th>
                              <th>Recorded Date</th>
                              <th>Borrower / Trustor</th>
                              <th>Address</th>
                              <th>City</th>
                              <th>Principal Bal</th>
                              <th className="right-align">System Date</th>
                            </>
                          )}
                        </tr>
                      </thead>
                      <tbody>
                        {(() => {
                          // Use search results if searching, otherwise use filtered rows
                          const activeRows = searchQuery && searchQuery.length >= 2 ? searchResults : rows;
                          const displayRows = activeRows.filter(r => {
                            if (addressFilter === "with") return r.property_address && r.property_address.trim() !== "";
                            if (addressFilter === "without") return !r.property_address || r.property_address.trim() === "";
                            return true;
                          });

                          return displayRows.length === 0 ? (
                            <tr>
                              <td colSpan={isGrahamView ? 6 : isMaricopaView ? 7 : 8} className="empty-state">
                                {searchQuery && searchQuery.length >= 2
                                  ? "No records found matching your search."
                                  : "No records found for the selected view."}
                              </td>
                            </tr>
                          ) : (
                            displayRows.map((row) => {
                              const doc = row.documents || {};
                              return (
                                <tr key={`${row.id}-${doc.recording_number || "none"}`}>
                                  {isGrahamView ? (
                                    <>
                                      <td className="fw-medium">{doc.recording_number || "-"}</td>
                                      <td>{doc.recording_date || "-"}</td>
                                      <td className="td-truncate" title={row.grantors}>{row.grantors || "-"}</td>
                                      <td className="td-truncate" title={row.grantees}>{row.grantees || "-"}</td>
                                      <td className="fw-medium">{row.original_principal_balance || row.principal_amount || "-"}</td>
                                      <td className="td-truncate" title={row.property_address}>{row.property_address || "-"}</td>
                                    </>
                                  ) : isMaricopaView ? (
                                    <>
                                      <td>{row.trustor_1_full_name || "-"}</td>
                                      <td>{row.trustor_2_full_name || "-"}</td>
                                      <td className="td-truncate" title={row.property_address}>{row.property_address || "-"}</td>
                                      <td>{row.address_city || "-"}</td>
                                      <td>{row.address_state || "-"}</td>
                                      <td className="fw-medium">{doc.recording_number || "-"}</td>
                                      <td>{doc.recording_date || "-"}</td>
                                      <td className="fw-medium">{row.original_principal_balance || "-"}</td>
                                    </>
                                  ) : (
                                    <>
                                      <td className="fw-medium">{doc.recording_number || "-"}</td>
                                      <td><span className="doc-badge">{doc.document_type || "-"}</span></td>
                                      <td>{doc.recording_date || "-"}</td>
                                      <td>{row.trustor_1_full_name || row.trustor_2_full_name || "-"}</td>
                                      <td className="td-truncate" title={row.property_address}>{row.property_address || "-"}</td>
                                      <td>{row.address_city || "-"}</td>
                                      <td className="fw-medium">{row.original_principal_balance || "-"}</td>
                                      <td className="right-align text-xs text-muted">{formatDate(row.created_at)}</td>
                                    </>
                                  )}
                                </tr>
                              );
                            })
                          );
                        })()}
                      </tbody>
                    </table>
                  )}
                </div>
              </>
            ) : (
              <div className="empty-panel">
                <div className="empty-icon-wrap">
                  <Icon path={ICONS.settings} />
                </div>
                <h3>{COUNTIES.find(c => c.key === activeCounty)?.name} Integration Pending</h3>
                <p>Scraping and OCR ingestion pipelines for this county are currently scheduled on the roadmap.</p>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
