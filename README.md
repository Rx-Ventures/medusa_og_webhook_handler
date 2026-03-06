###### Pydantic rules 

- always create a base for schema followed by 
reqeuest,response,update,deleted 

- reference  schemas/payment.py

###### GIT RULES 

1. Feature Development
   ├─ git checkout develop
   ├─ git pull origin develop
   ├─ git checkout -b feature/add-new-cart
   ├─ (work, commit, push)
   └─ Create PR: feature/add-new-cart → develop
2. Merge to Develop (after PR approved)
   ├─ Merge PR
   ├─ git checkout develop
   ├─ git pull origin develop
   └─ Auto-deploy to STAGING environment
3. Test on Staging
   ├─ QA tests the feature
   ├─ If bugs found → create bugfix branch from develop
   └─ If all good → ready for production!
4. Release to Production (Main)
   ├─ git checkout main
   ├─ git pull origin main
   ├─ git merge develop
   ├─ git tag -a v0.2.0 -m "Release v0.2.0 - Added new cart"
   ├─ git push origin main
   ├─ git push origin v0.2.0
   └─ Deploy v0.2.0 tag to PRODUCTION

###### Clearing cache

rm -rf node_modules
rm -f package-lock.json
npm cache clean --force
npm install

###### Notes 

- always right clean code
- delete weird comments 
- right ready to test code

###### Database Access Strategy

###### ORM (SQLAlchemy)

- Tables we own and created via Alembic
- webhook_events table
- Full CRUD operations
- Type safety and validation
- Migrations managed by us
- Raw SQL (asyncpg)

###### Medusa's existing tables

- Read-only queries to Medusa data
- Complex joins and aggregations
- No model maintenance needed
- Schema changes handled by Medusa

###### Simple Rule

- We created it → ORM
- Medusa created it → Raw SQL


###### PROD ( will create after successfull dev )

docker-compose -f docker-compose.prod.yml down 
docker-compose -f docker-compose.prod.yml build --no-cache
docker-compose -f docker-compose.prod.yml up
docker-compose -f docker-compose.prod.yml logs -f app

###### DEV

docker-compose down
docker-compose build --no-cache
docker-compose up
docker exec -it app alembic upgrade head

###### Alembic Commands

###### Create & Run Migrations
- Create new migration: `alembic revision --autogenerate -m "description of change"`
- Run all pending migrations: `alembic upgrade head`
- Run inside Docker: `docker-compose exec app alembic upgrade head`

###### Undo Migrations
- Undo last migration: `alembic downgrade -1`
- Undo all migrations: `alembic downgrade base`

###### Check Status
- Check current version: `alembic current`
- See migration history: `alembic history`

###### After Deleting Database
- Just run: `alembic upgrade head`
- This re-applies all migrations from scratch


###### Fresh db commands

- be sure to delete versions!
- be sure to use session pooler 
- then do alembic revision --autogenerate -m "create webhook_events"
- alembic upgrade head

###### changes 

- if you change ecs.tf 
   - you need terraform apply 

- changes in secret 
   - redeploy 

- changes in code 
   docker build and deploy


---

## NetValve Payment Gateway Routes


All routes use the `/api/v1/netvalve/...` prefix convention.

### Route Reference

| Method | Path | Description | Source |
|--------|------|-------------|--------|
| POST | `/api/v1/netvalve/hpf/session` | Initialize HPF session (5-step waterfall) | `route.ts` POST |
| GET | `/api/v1/netvalve/hpf/session` | HPF session (GET convenience) | `route.ts` GET |
| POST | `/api/v1/netvalve/payment` | Authorize payment via POST /sale | `service.ts` authorizePayment |
| POST | `/api/v1/netvalve/capture` | Capture authorized payment | `service.ts` capturePayment |
| POST | `/api/v1/netvalve/refund` | Refund captured payment | `service.ts` refundPayment |
| POST | `/api/v1/netvalve/cancel` | Cancel (void) payment | `service.ts` cancelPayment |
| POST | `/api/v1/netvalve/webhook` | Receive NetValve webhook events | `service.ts` getWebhookActionAndData |
| GET | `/api/v1/netvalve/status` | Check payment status | `service.ts` getPaymentStatus |

### File Structure

```
app/
├── api/v1/endpoints/netvalve/
│   ├── __init__.py
│   ├── router.py        # Aggregates all sub-routers
│   ├── hpf.py           # HPF session init (POST + GET)
│   ├── payment.py       # Payment authorize/sale
│   ├── capture.py       # Capture
│   ├── refund.py        # Refund
│   ├── cancel.py        # Cancel/void
│   ├── webhook.py       # Webhook receiver
│   └── status.py        # Status lookup
├── schemas/
│   └── netvalve.py      # All Pydantic request/response models
└── services/
    └── netvalve_service.py  # Business logic + NetValve API calls
```

### Environment Variables

Required:
- `NETVALVE_API_KEY` — API key for NetValve gateway

Optional:
- `NETVALVE_CLIENT_ID` — Client identifier header
- `NETVALVE_SITE_ID` — NetValve site/merchant ID
- `NETVALVE_MID_ID_EUR` / `NETVALVE_MID_ID_USD` / `NETVALVE_MID_ID_PHP` — Currency-specific MID IDs
- `NETVALVE_ENVIRONMENT` — `sandbox` or `production` (default: production)
- `NETVALVE_BASE_URL` — Override base URL for payment API
- `NETVALVE_BACKOFFICE_API_URL` — Override backoffice URL
- `NETVALVE_BASIC_AUTH_USERNAME` / `NETVALVE_BASIC_AUTH_PASSWORD` — Backoffice credentials
- `NETVALVE_HPF_SCRIPT_SRC` — Static HPF script URL override
- `NETVALVE_HPP_DIRECT_URL` — Pre-built HPP redirect URL
- See `app/core/config.py` for the full list of 30+ NetValve env vars

