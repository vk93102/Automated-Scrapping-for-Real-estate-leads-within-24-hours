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
  d.setHours(0, 0, 0, 0);
  // Include exactly `days` calendar days including today.
  d.setDate(d.getDate() - (days - 1));
  return d.toISOString();
}

function sqlParsedDateFromText(textExpr) {
  return `(
    CASE
      WHEN ${textExpr} IS NULL OR BTRIM(${textExpr}) = '' THEN NULL
      WHEN ${textExpr} ~ '^\\d{4}-\\d{2}-\\d{2}$' THEN (${textExpr})::date
      WHEN ${textExpr} ~ '^\\d{4}-\\d{2}-\\d{2}T' THEN (substring(${textExpr} from 1 for 10))::date
      WHEN ${textExpr} ~ '^\\d{1,2}/\\d{1,2}/\\d{4}$' THEN to_date(${textExpr}, 'MM/DD/YYYY')
      WHEN ${textExpr} ~ '^\\d{1,2}-\\d{1,2}-\\d{4}$' THEN to_date(${textExpr}, 'MM-DD-YYYY')
      ELSE NULL
    END
  )`;
}

export async function GET(request) {
  try {
    const { searchParams } = new URL(request.url);
    const query = (searchParams.get("q") || "").trim();
    const county = searchParams.get("county") || "maricopa";
    const range = searchParams.get("range") || (county === "maricopa" ? "day" : "all");
    const sinceIso = getSinceIso(range);
    
    if (!query || query.length < 2) {
      return NextResponse.json({ rows: [] });
    }

    const searchTerm = `%${query}%`;
    let sql = "";

    if (county === "graham") {
      const recordingDateText = "NULLIF(BTRIM(recording_date::text), '')";
      const recordingDateParsed = sqlParsedDateFromText(recordingDateText);
      const effectiveTs = `COALESCE((${recordingDateParsed})::timestamptz, created_at)`;
      sql = `
        SELECT
          id,
          source_county,
          document_id,
          recording_number,
          recording_date,
          document_type,
          grantors,
          grantees,
          trustor,
          trustee,
          beneficiary,
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
          property_address,
          NULL as address_city,
          NULL as address_state,
          NULL as address_zip,
          NULL as sale_date,
          principal_amount as original_principal_balance,
          principal_amount,
          detail_url,
          image_urls,
          ocr_method,
          ocr_chars,
          used_groq,
          groq_model as llm_model
        FROM graham_leads
        WHERE (
          recording_number ILIKE $1
          OR grantors ILIKE $1
          OR grantees ILIKE $1
          OR trustor ILIKE $1
          OR property_address ILIKE $1
        )
        AND ($2::timestamptz IS NULL OR ${effectiveTs} >= $2::timestamptz)
        ORDER BY created_at DESC
        LIMIT 100;
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
      const recordingDateText = `COALESCE(
        NULLIF(BTRIM(recording_date::text), ''),
        NULLIF(BTRIM(raw_record->>'recordingDate'), ''),
        NULLIF(BTRIM(raw_record->>'recording_date'), '')
      )`;
      const recordingDateParsed = sqlParsedDateFromText(recordingDateText);
      const effectiveTs = `COALESCE((${recordingDateParsed})::timestamptz, created_at)`;
      sql = `
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
          property_address,
          NULL as address_city,
          NULL as address_state,
          NULL as address_zip,
          NULL as sale_date,
          principal_amount as original_principal_balance,
          NULL as principal_amount,
          NULL as detail_url,
          NULL as image_urls,
          NULL as ocr_method,
          NULL::integer as ocr_chars,
          NULL::boolean as used_groq,
          groq_model as llm_model
        FROM ${tableName}
        WHERE (
          recording_number ILIKE $1
          OR trustor ILIKE $1
          OR property_address ILIKE $1
        )
        AND ($2::timestamptz IS NULL OR ${effectiveTs} >= $2::timestamptz)
        ORDER BY created_at DESC
        LIMIT 100;
      `;
    } else {
      // Maricopa and others
      const effectiveTs = "COALESCE(d.recording_date::timestamptz, d.created_at)";
      sql = `
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
        FROM maricopa.documents d
        LEFT JOIN maricopa.properties p ON p.document_id = d.id
        WHERE (
          d.recording_number ILIKE $1
          OR p.trustor_1_full_name ILIKE $1
          OR p.trustor_2_full_name ILIKE $1
          OR p.property_address ILIKE $1
          OR p.address_city ILIKE $1
        )
        AND ($2::timestamptz IS NULL OR ${effectiveTs} >= $2::timestamptz)
        ORDER BY d.created_at DESC
        LIMIT 100;
      `;
    }

    const { rows } = await pool.query(sql, [searchTerm, sinceIso]);

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
      query,
      range,
      total: formattedRows.length,
      rows: formattedRows,
    });
  } catch (err) {
    console.error("Search error:", err);
    return NextResponse.json(
      { error: err?.message || "Search failed" },
      { status: 500 }
    );
  }
}
