-- vacuum_analyze.sql: Maintenance script for PostgreSQL
VACUUM ANALYZE threads;
VACUUM ANALYZE posts;
VACUUM ANALYZE source_files;
VACUUM ANALYZE boards;
VACUUM ANALYZE thread_source_files;
VACUUM ANALYZE import_logs;
