// Bulk-load a CSV into the ledger via Postgres COPY.
// Postgres parses the CSV itself, so quoting/embedded commas/newlines are handled
// correctly without a JS CSV parser.
//
// usage: node load_csv.js --file <path> --target company|people [--dry-run]
const fs = require('fs');
const {Client} = require('pg');
const copyFrom = require('pg-copy-streams').from;

const arg = n => { const i = process.argv.indexOf('--'+n); return i>-1 ? process.argv[i+1] : null; };
const file = arg('file'), target = arg('target');
const dry = process.argv.includes('--dry-run');
if (!file || !['company','people'].includes(target)) {
  console.error('usage: node load_csv.js --file <path> --target company|people [--dry-run]');
  process.exit(2);
}
const CFG = {
  company: {stg:'stg_company_import', led:'company_ledger',
            cols:['Name','Company Domain'],
            key:'domain_key',   norm:'normalize_domain',   idcol:'Company Domain', paycol:'Name'},
  people:  {stg:'stg_people_import',  led:'people_ledger',
            cols:['Full Name','Linkedin Profile'],
            key:'linkedin_key', norm:'normalize_linkedin', idcol:'Linkedin Profile', paycol:'Full Name'},
}[target];

const q = c => '"'+c+'"';
const c = new Client({host:process.env.PGHOST,port:5432,user:'postgres',
  password:process.env.PGPASSWORD,database:'postgres',
  ssl:{rejectUnauthorized:false},connectionTimeoutMillis:20000, statement_timeout:0});

(async () => {
  await c.connect();

  // Read the CSV header and match it to the expected columns, so a column-order
  // difference or a stray extra column is caught before loading rather than
  // silently shifting data into the wrong field.
  const head = fs.readFileSync(file,'utf8').split(/\r?\n/)[0];
  console.log('CSV header : ' + head);
  const hdr = head.replace(/^﻿/,'').split(',').map(h=>h.trim().replace(/^"|"$/g,''));
  const missing = CFG.cols.filter(x => !hdr.includes(x));
  if (missing.length) {
    console.error('\nERROR: CSV is missing required column(s): ' + missing.map(q).join(', '));
    console.error('       found: ' + hdr.map(q).join(', '));
    console.error('       Rename the CSV headers to match EXACTLY (spaces and capitals included).');
    await c.end(); process.exit(1);
  }
  const extra = hdr.filter(h => !CFG.cols.includes(h));
  if (extra.length) console.log('note       : ignoring extra column(s): ' + extra.map(q).join(', '));

  await c.query('begin');
  await c.query(`create table if not exists public.${CFG.stg} (${CFG.cols.map(x=>q(x)+' text').join(', ')})`);
  await c.query(`truncate public.${CFG.stg}`);

  // COPY only the columns we need, in the CSV's own order
  const order = hdr.filter(h => CFG.cols.includes(h));
  const copyCols = hdr.map(h => CFG.cols.includes(h) ? q(h) : null);
  // If the CSV has extra columns, COPY cannot skip them -> stage everything as text.
  let stgCols = order;
  if (extra.length) {
    await c.query(`drop table public.${CFG.stg}`);
    await c.query(`create table public.${CFG.stg} (${hdr.map(x=>q(x)+' text').join(', ')})`);
    stgCols = hdr;
  }

  const before = Date.now();
  await new Promise((res, rej) => {
    const s = c.query(copyFrom(
      `copy public.${CFG.stg} (${stgCols.map(q).join(', ')}) from stdin with (format csv, header true)`));
    const r = fs.createReadStream(file);
    r.on('error', rej); s.on('error', rej); s.on('finish', res); r.pipe(s);
  });
  const staged = (await c.query(`select count(*)::int n from public.${CFG.stg}`)).rows[0].n;
  console.log(`staged     : ${staged} rows in ${((Date.now()-before)/1000).toFixed(1)}s`);

  // Normalization happens HERE, via the SAME function the RPC uses, so a row
  // loaded from CSV and the same record arriving later from Clay produce an
  // identical key. That agreement is the entire point of normalizing in the
  // database rather than cleaning the CSV.
  const ID = q(CFG.idcol), PAY = q(CFG.paycol), NK = `public.${CFG.norm}(${ID})`;

  const stats = (await c.query(`select
      count(*) filter (where ${NK} is null)::int unusable,
      count(distinct ${NK})::int keys,
      count(distinct ${ID})::int raw_variants,
      count(*) filter (where ${ID} like '/%' or coalesce(${PAY},'') like '/%')::int refs
    from public.${CFG.stg}`)).rows[0];
  console.log(`distinct raw values as-is  : ${stats.raw_variants}`);
  console.log(`distinct keys normalized   : ${stats.keys}`);
  console.log(`  collapsed by normalizing : ${stats.raw_variants - stats.keys}`);
  console.log(`unusable (no ${CFG.idcol}) : ${stats.unusable} rows -> NOT blocklisted`);
  console.log(`bad refs ('/...' literals) : ${stats.refs}`);
  if (stats.refs > 0) {
    console.error('\nERROR: CSV contains unresolved Clay column references. Aborting.');
    await c.query('rollback'); await c.end(); process.exit(1);
  }

  const t0 = (await c.query(`select count(*)::int n from public.${CFG.led}`)).rows[0].n;
  // DISTINCT ON picks one raw value per normalized key. ORDER BY length() prefers
  // the tidiest surviving spelling ('acme.com' over 'https://www.acme.com/') and
  // makes the choice deterministic instead of arbitrary.
  await c.query(`insert into public.${CFG.led} (${CFG.key}, ${PAY}, ${ID})
    select distinct on (${NK}) ${NK},
           nullif(btrim(coalesce(${PAY},'')),''),
           nullif(btrim(coalesce(${ID},'')),'')
    from public.${CFG.stg}
    where ${NK} is not null
    order by ${NK}, length(${ID}), ${ID}
    on conflict (${CFG.key}) do nothing`);
  const t1 = (await c.query(`select count(*)::int n from public.${CFG.led}`)).rows[0].n;

  console.log(`\nledger     : ${t0} -> ${t1}  (+${t1-t0} new)`);
  const size = (await c.query(`select pg_size_pretty(pg_total_relation_size('public.${CFG.led}')) s,
                                      pg_size_pretty(pg_database_size(current_database())) d`)).rows[0];
  console.log(`size       : ${CFG.led} ${size.s},  database total ${size.d} of 500 MB`);

  await c.query(`drop table if exists public.${CFG.stg}`);
  if (dry) { await c.query('rollback'); console.log('\nDRY RUN -- rolled back, nothing persisted.'); }
  else     { await c.query('commit');   console.log('\nCommitted.'); }
  await c.end();
})().catch(async e => { try{await c.query('rollback')}catch{}; console.error('FAILED: '+e.message); process.exit(1); });
