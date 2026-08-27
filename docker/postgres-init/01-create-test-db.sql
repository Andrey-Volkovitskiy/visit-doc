-- Runs once, only on a fresh `postgres_data` volume (per the official Postgres image's
-- /docker-entrypoint-initdb.d convention). Gives the chat service's test suite its own
-- database, isolated from the `visitdoc_chat` one used for manual/dev work.
CREATE DATABASE visitdoc_chat_test;
