-- Runs once, only on a fresh `postgres_data` volume, alongside 01-create-test-db.sql.
-- The scheduling service owns its own schema and migration history, so it gets its own
-- logical database (plus an isolated one for its test suite) rather than sharing chat's.
-- Same container in development, separate databases - no cross-database joins or foreign
-- keys exist or can exist between them.
CREATE DATABASE visitdoc_scheduler;
CREATE DATABASE visitdoc_scheduler_test;
