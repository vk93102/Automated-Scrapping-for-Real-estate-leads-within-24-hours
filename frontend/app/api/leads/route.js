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
    if (county === "graham") {
      const tableName = "graham_leads";
      query = `
        SELECT
          id,
          source_county,
          document_id,
          recording_number,
          recording_date,
          document_type,
          COALESCE(
            NULLIF(BTRIM(grantors), ''),
            NULLIF(BTRIM(raw_record->>'grantors'), '')
          ) AS grantors,
          COALESCE(
            NULLIF(BTRIM(grantees), ''),
            NULLIF(BTRIM(raw_record->>'grantees'), '')
          ) AS grantees,
          COALESCE(
            NULLIF(BTRIM(trustor), ''),
            NULLIF(BTRIM(raw_record->>'trustor'), '')
          ) AS trustor,
          COALESCE(
            NULLIF(BTRIM(trustee), ''),
            NULLIF(BTRIM(raw_record->>'trustee'), '')
          ) AS trustee,
          COALESCE(
            NULLIF(BTRIM(beneficiary), ''),
            NULLIF(BTRIM(raw_record->>'beneficiary'), '')
          ) AS beneficiary,
          created_at,
          updated_at,
          COALESCE(
            NULLIF(BTRIM(trustor), ''),
            NULLIF(BTRIM(SPLIT_PART(grantors, '|', 1)), ''),
            NULLIF(BTRIM(grantors), '')
          ) AS trustor_1_full_name,
          COALESCE(
            NULLIF(BTRIM(SPLIT_PART(grantors, '|', 2)), ''),
            NULL
          ) AS trustor_2_full_name,
          COALESCE(
            NULLIF(BTRIM(property_address), ''),
            NULLIF(BTRIM(raw_record->>'propertyAddress'), '')
          ) AS property_address,
          NULL as address_city,
          NULL as address_state,
          NULL as address_zip,
          NULL as sale_date,
          COALESCE(
            NULLIF(BTRIM(principal_amount), ''),
            NULLIF(BTRIM(raw_record->>'principalAmount'), '')
          ) AS original_principal_balance,
          principal_amount,
          detail_url,
          image_urls,
          ocr_method,
          ocr_chars,
          used_groq,
          groq_model as llm_model
        FROM ${tableName}
        WHERE ($1::timestamptz IS NULL OR created_at >= $1::timestamptz)
        ORDER BY created_at DESC
        LIMIT 10000;
      `;
    } else if (["la-paz", "navajo", "santa-cruz", "greenlee", "cochise"].includes(county)) {
      const tableMap = {
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
          source_county,
          document_id,
          recording_number,
          recording_date,
          document_type,
          NULL as grantors,
          NULL as grantees,
          NULL as trustor,
          NULL as trustee,
          NULL as beneficiary,
          created_at,
          updated_at,
          COALESCE(
            NULLIF(BTRIM(trustor), ''),
            NULLIF(BTRIM(raw_record->>'trustor'), '')
          ) AS trustor_1_full_name,
          NULL as trustor_2_full_name,
          COALESCE(
            NULLIF(BTRIM(property_address), ''),
            NULLIF(BTRIM(raw_record->>'propertyAddress'), '')
          ) AS property_address,
          NULL as address_city,
          NULL as address_state,
          NULL as address_zip,
          NULL as sale_date,
          COALESCE(
            NULLIF(BTRIM(principal_amount), ''),
            NULLIF(BTRIM(raw_record->>'principalAmount'), '')
          ) AS original_principal_balance,
          NULL as principal_amount,
          NULL as detail_url,
          NULL as image_urls,
          NULL as ocr_method,
          NULL::integer as ocr_chars,
          NULL::boolean as used_groq,
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
          NULL as source_county,
          CAST(d.id as text) as document_id,
          d.recording_number,
          d.recording_date,
          d.document_type,
          NULL as grantors,
          NULL as grantees,
          NULL as trustor,
          NULL as trustee,
          NULL as beneficiary,
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
          p.original_principal_balance as principal_amount,
          NULL as detail_url,
          NULL as image_urls,
          NULL as ocr_method,
          NULL::integer as ocr_chars,
          NULL::boolean as used_groq,
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
      source_county: r.source_county || null,
      document_id: r.document_id || r.id,
      trustor_1_full_name: r.trustor_1_full_name || null,
      trustor_2_full_name: r.trustor_2_full_name || null,
      grantors: r.grantors || null,
      grantees: r.grantees || null,
      trustor: r.trustor || null,
      trustee: r.trustee || null,
      beneficiary: r.beneficiary || null,
      property_address: r.property_address || null,
      address_city: r.address_city || null,
      address_state: r.address_state || null,
      address_zip: r.address_zip || null,
      sale_date: r.sale_date || null,
      original_principal_balance: r.original_principal_balance || null,
      principal_amount: r.principal_amount || null,
      detail_url: r.detail_url || null,
      image_urls: r.image_urls || null,
      ocr_method: r.ocr_method || null,
      ocr_chars: r.ocr_chars ?? null,
      used_groq: r.used_groq ?? null,
      llm_model: r.llm_model || null,
      created_at: r.created_at,
      updated_at: r.updated_at,
      documents: {
        recording_number: r.recording_number,
        recording_date: r.recording_date,
        document_type: r.document_type,
        grantors: r.grantors || null,
        grantees: r.grantees || null,
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

