# KernelCI Dashboard

Official Dashboard for [KCIDB](https://docs.kernelci.org/components/kcidb/).
Available at [dashboard.kernelci.org](https://dashboard.kernelci.org).

The KernelCI Dashboard is a web application created to visualize results from
static checks, builds, boots and tests related to the Linux kernel CI/test ecosystem.
All that data is provided by the [KCIDB](https://docs.kernelci.org/components/kcidb/) project
by the [KernelCI Foundation](https://kernelci.org/).

## Repository structure

- `dashboard/` - Frontend web application built with [React](https://react.dev/) + [TypeScript](https://www.typescriptlang.org/).
  Check its [README](dashboard/README.md) for more info.
- `backend` - API built with [Django](https://www.djangoproject.com/) + [DRF](https://www.django-rest-framework.org/).
  Check its [README](backend/README.md) for more info.
- `docs/` - General documentation and glossary for the project. Implementation specific documentation can be found in the respective sub-directories.
- `k6` - TODO
- `monitoring` - TODO
- `proxy/` - TODO

## Quick run

If you want to just run the project, you can try out pre-built images with the [docker-compose-next.yml](./docker-compose-next.yml) file. This pulls images from GHCR and runs them locally without needing to rebuild them. You may still need to set up environment variables, so read the docs.

## Development setup

### Local

1. Follow setup instructions for the [Dashboard](dashboard/README.md#setup) and [Backend](backend/README.md#setup) to get the services running.

### Docker Compose

Alternatively, you can use Docker Compose to run the services in containers.
This is the method used in production and is recommended for development as well, as it more closely matches the production environment.

#### Pre-requisites

- [Docker](https://docs.docker.com/get-docker/)
- [Docker Compose](https://docs.docker.com/compose/install/)

#### Setup

1. Copy `.env.example` to `.env` and set the environment variables as needed.
   The `.env` file is used by docker compose to set environment variables for the services.
2. Set `COMPOSE_FILE` to `compose.yaml:compose.override.dev.yaml` in the `.env` file.
   The `dev` override sets up live code reloading for backend and frontend services.
3. (Optional) Run `docker compose build` to build images from local Dockerfiles.
   Some code changes require rebuilding the images to be applied.
4. Run `docker compose up --wait` to start the services.
5. Access the dashboard at `http://localhost:8080` and the API at `http://localhost:8080/api`.
6. Check `.env` and the compose files for other options.

### Manual configuration checks

If you want to verify container/deployment environment settings before running services, use:

- `docker compose run --rm backend python3 manage.py verify_env` for DB/Redis/Email + storage + env/secrets checks
- [docs/verify_env.md](docs/verify_env.md) for detailed examples, including test email sending to a specific destination
  - Destination is required with `--send-test-email` and `--to-email`.

## Contributing

Check out our [CONTRIBUTING.md](/CONTRIBUTING.md), and there is an [onboarding guide](docs/Onboarding.md) to help get acquainted with the project. Contributions are welcome!

For a local development environment with live reload (backend + frontend), see [docs/dev-environment.md](docs/dev-environment.md). That Docker-based workflow uses a root `.env` file plus `dashboard/.env`; the backend-specific manual setup above still uses `.env.backend`. Use the env files required by the workflow you choose.
