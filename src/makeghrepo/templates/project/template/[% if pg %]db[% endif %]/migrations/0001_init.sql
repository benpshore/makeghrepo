-- Migrations run in filename order: in CI, and on first `docker compose up`.
create table if not exists app_meta (
    setting_name text primary key,
    setting_value text not null,
    updated_at timestamptz not null default now()
);
