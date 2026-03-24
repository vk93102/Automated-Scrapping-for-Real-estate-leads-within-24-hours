#!/usr/bin/env python3
import os
from pathlib import Path
import psycopg

# Load .env
for line in Path('.env').read_text().splitlines():
    if line.strip() and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1)
        os.environ[k.strip()] = v.strip().strip('"').strip("'")

# Connect to database
conn = psycopg.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()

# Get Santa Cruz table columns
cur.execute("""
  SELECT column_name, data_type 
  FROM information_schema.columns 
  WHERE table_schema='public' AND table_name='santacruz_leads' 
  ORDER BY ordinal_position
""")

cols = cur.fetchall()
print(f'Total columns in santacruz_leads: {len(cols)}')
print()

# Look for recording_number and document_type
has_recording_number = any(c[0] == 'recording_number' for c in cols)
has_document_type = any(c[0] == 'document_type' for c in cols)

print(f'Has recording_number column: {has_recording_number}')
print(f'Has document_type column: {has_document_type}')

# Show all columns with 'record' or 'document' or 'type'
print()
print('Columns matching record/document/type:')
for col in cols:
    if 'record' in col[0].lower() or 'document' in col[0].lower() or 'type' in col[0].lower():
        print(f'  {col[0]}: {col[1]}')

# Show first 20 columns
print()
print('All columns:')
for col in cols:
    print(f'  {col[0]}: {col[1]}')

# Now let's check a sample row to see if data exists
print()
print('Sample data from santacruz_leads:')
cur.execute("""
  SELECT id, raw_record->>'recordingNumber', raw_record->>'recording_number', 
         raw_record->>'documentType', raw_record->>'document_type',
         recording_number, document_type
  FROM santacruz_leads 
  LIMIT 3
""")

for row in cur.fetchall():
    print(f'  ID: {row[0]}')
    print(f'    raw_record->recordingNumber: {row[1]}')
    print(f'    raw_record->recording_number: {row[2]}')
    print(f'    raw_record->documentType: {row[3]}')
    print(f'    raw_record->document_type: {row[4]}')
    print(f'    recording_number column: {row[5]}')
    print(f'    document_type column: {row[6]}')
    print()

cur.close()
conn.close()
