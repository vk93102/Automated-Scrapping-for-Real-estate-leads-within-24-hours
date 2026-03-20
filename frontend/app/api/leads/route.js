import { NextResponse } from "next/server";
import { Pool } from "pg";

export const dynamic = "force-dynamic";

const pool = new Pool({
  connectionString: process.env.DATABASE_URL,
  ssl: { rejectUnauthorized: false }
});

const RANGE_MAP = {
  day: 1,
  week: 7,
  month: 30,
  all: null,
};

function getSinceIso(range) {
  const days = RANGE_MAP[range] ?? null;
  if (!days) return null;
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString();
}

export async function GET(request) {
  try {
    const { searchParams } = new URL(request.url);
    const range = searchParams.get("range") || "all";
    const county = searchParams.get("county") || "maricopa";
    const sinceIso = getSinceIso(range);

    let query = "";
    if (["graham", "la-paz", "navajo", "santa-cruz", "greenlee", "cochise"].includes(county)) {
      const tableMap = {
        "graham": "graham_leads",
        "la-paz": "lapaz_leads",
        "navajo": "navajo_leads",
        "santa-cruz": "santacruz_leads",
        "greenlee": "greenlee_leads",
        "cochise": "cochise_leads"
      };
      const tableName = tableMap[county];
      query = `
        SELECT
          id,
          recording_number,
          recording_date,
          document_type,
          created_at,
          updated_at,
          trustor as trustor_1_full_name,
          NULL as trustor_2_full_name,
          property_address,
          NULL as address_city,
          NULL as address_state,
          NULL as address_zip,
          NULL as sale_date,
          principal_amount as original_principal_balance,
          groq_model as llm_model
        FROM ${tableName}
        WHERE ($1::timestamptz IS NULL OR created_at >= $1::timestamptz)
        ORDER BY created_at DESC
        LIMIT 10000;
      `;
    } else {
      query = `
        SELECT
          d.id,
          d.recording_number,
          d.recording_date,
          d.document_type,
          d.created_at,
          d.updated_at,
          p.trustor_1_full_name,
          p.trustor_2_full_name,
          p.property_address,
          p.address_city,
          p.address_state,
          p.address_zip,
          p.sale_date,
          p.original_principal_balance,
          p.llm_model
        FROM documents d
        LEFT JOIN properties p ON p.document_id = d.id
        WHERE ($1::timestamptz IS NULL OR d.created_at >= $1::timestamptz)
        ORDER BY d.created_at DESC
        LIMIT 10000;
      `;
    }

    const { rows } = await pool.query(query, [sinceIso]);

    const formattedRows = rows.map(r => ({
      id: r.id,
      document_id: r.id,
      trustor_1_full_name: r.trustor_1_full_name || null,
      trustor_2_full_name: r.trustor_2_full_name || null,
      property_address: r.property_address || null,
      address_city: r.address_city || null,
      address_state: r.address_state || null,
      address_zip: r.address_zip || null,
      sale_date: r.sale_date || null,
      original_principal_balance: r.original_principal_balance || null,
      llm_model: r.llm_model || null,
      created_at: r.created_at,
      updated_at: r.updated_at,
      documents: {
        recording_number: r.recording_number,
        recording_date: r.recording_date,
        document_type: r.document_type,
        created_at: r.created_at,
      },
    }));

    return NextResponse.json({
      range,
      total: formattedRows.length,
      rows: formattedRows,
    });
  } catch (err) {
    console.error("Database fetch error:", err);
    return NextResponse.json(
      { error: err?.message || "Failed to fetch leads" },
      { status: 500 }
    );
  }
}

