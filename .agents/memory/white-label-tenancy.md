---
name: White-label multi-tenancy
description: How the salon app implements per-tenant branding and data isolation
---

# White-label multi-tenancy

## Schema
- `tenants` table: id, name, slug, tagline, logo_filename, primary_color, accent_color, bg_color, phone, email, address, is_active
- `admin_users.tenant_id` and `branches.tenant_id` added via `_migrate_db()` with DEFAULT 1
- Default tenant (id=1, slug='vanshika') seeded by `_seed_default_tenant()`

## Tenant resolution
- `@app.before_request load_tenant()` sets `g.tenant` from `session['tenant_id']` (default 1)
- `@app.context_processor inject_tenant()` exposes `tenant` and `tenant_logo_url` to all templates
- Static files (static endpoint) are skipped — no DB call

## Admin session keys
- `session['tenant_id']` — the tenant the logged-in admin belongs to (NULL for superadmin)
- `session['is_superadmin']` — True for the platform super-admin role

## Superadmin
- Role stored as `role='superadmin'` with `tenant_id=NULL` in admin_users
- Created at startup if `SUPERADMIN_EMAIL` + `SUPERADMIN_PASSWORD` env vars are set
- Routes at `/superadmin/*`; decorator `superadmin_required`
- login redirect → `/superadmin/tenants` (not `/admin/dashboard`)

## Branding
- CSS variables `--gold`, `--gold2`, `--page-bg` in base.html/:root come from tenant colors
- All three base templates (base.html, admin_base.html, staff_portal_base.html) use `{{ tenant.name }}`, `{{ tenant.tagline }}`, `{{ tenant_logo_url }}`
- Tenant owner edits branding at `/admin/branding`
- Logos uploaded to `static/logos/` as `tenant_{id}_logo.{ext}`

## Data isolation
- `get_admin_context()` filters branches by `tenant_id` from session
- `admin_team*` routes scope admin_users by `tenant_id`
- Other data (appointments, customers, invoices, etc.) is already scoped by branch_id, and branches are tenant-scoped

**Why:**
Single DB file, so tenant isolation uses FK relationships rather than separate schemas. Existing data migrated to tenant_id=1 on first startup.
