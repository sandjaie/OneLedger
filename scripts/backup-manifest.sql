-- Structural evidence manifest (spec "Structural evidence manifest", Phase 2.4). Run inside the
-- same REPEATABLE READ transaction whose exported snapshot pg_dump restores with, so the manifest
-- and the dump describe identical data. One row, one JSON column, no row content, identifier or
-- filename — only counts, named NUMERIC sums as decimal strings, and hashes.
--
-- The caller supplies two psql variables computed from the documents archive being written
-- alongside this same run: :'docs_count' and :'docs_hash'.
--
-- Table counts are computed dynamically (query_to_xml is core Postgres, no extension) so the
-- manifest never drifts from the schema it is backing up: a migration that adds a table is
-- covered here without this file changing.
SELECT json_build_object(
    'flywayVersion', (
        SELECT version FROM flyway_schema_history ORDER BY installed_rank DESC LIMIT 1
    ),
    'tableCounts', (
        SELECT coalesce(json_object_agg(t.tablename, cnt.row_count), '{}'::json)
          FROM pg_tables t,
               LATERAL (
                   SELECT (xpath('/row/c/text()',
                                  query_to_xml(format('SELECT count(*) AS c FROM %I.%I',
                                                       t.schemaname, t.tablename),
                                               false, true, '')
                                 ))[1]::text::bigint AS row_count
               ) cnt
         WHERE t.schemaname = 'public' AND t.tablename <> 'flyway_schema_history'
    ),
    'amountSums', json_build_object(
        'transaction.amount', (SELECT coalesce(sum(amount), 0)::text FROM transaction),
        'investment_transaction.net_amount',
            (SELECT coalesce(sum(net_amount), 0)::text FROM investment_transaction),
        'payslip.net_pay', (SELECT coalesce(sum(net_pay), 0)::text FROM payslip),
        'payslip.gross_earnings',
            (SELECT coalesce(sum(gross_earnings), 0)::text FROM payslip),
        'month_end_position.value_base',
            (SELECT coalesce(sum(value_base), 0)::text FROM month_end_position)
    ),
    'documents', json_build_object(
        'fileCount', :'docs_count'::int,
        'sha256OfSortedHashes', :'docs_hash'
    )
) AS manifest;
