# UI

SentinelPCB's review UI - the "simple UI prototype" required by the briefing. A single page:
upload a PCB image (or point it at one from `../simulation/images/`), it POSTs to the FastAPI
backend's `/inspections` endpoint (same contract `simulation/simulate_line.py` uses), and renders
the Orchestrator's decision with detected features drawn on the image.

**Run both halves:**

```bash
# terminal 1, from repo root
uv run uvicorn app.main:app --reload

# terminal 2, from ui/
npm install
ng serve
```

Then open `http://localhost:4200`. The backend must be running first and allows CORS from
`http://localhost:4200` by default (`app.settings.cors_allow_origins`).

Built with Angular's default Vite/esbuild-based `@angular/build:application` builder (no extra
config needed) and Tailwind CSS v4 (`.postcssrc.json` + `@import "tailwindcss";` in
`src/styles.css`).

**Known gap:** escalated cases currently only show a decision + rationale - there's no evidence
bundle (visual/measurement comparison, retrieved precedent) to display yet, since Explainability
& Review isn't implemented. See `docs/ADC-Group-Report-Team10-v1.md` §4.

---

This project was generated using [Angular CLI](https://github.com/angular/angular-cli) version 22.1.3.

## Development server

To start a local development server, run:

```bash
ng serve
```

Once the server is running, open your browser and navigate to `http://localhost:4200/`. The application will automatically reload whenever you modify any of the source files.

## Code scaffolding

Angular CLI includes powerful code scaffolding tools. To generate a new component, run:

```bash
ng generate component component-name
```

For a complete list of available schematics (such as `components`, `directives`, or `pipes`), run:

```bash
ng generate --help
```

## Building

To build the project run:

```bash
ng build
```

This will compile your project and store the build artifacts in the `dist/` directory. By default, the production build optimizes your application for performance and speed.

## Running unit tests

To execute unit tests with the [Vitest](https://vitest.dev/) test runner, use the following command:

```bash
ng test
```

## Running end-to-end tests

For end-to-end (e2e) testing, run:

```bash
ng e2e
```

Angular CLI does not come with an end-to-end testing framework by default. You can choose one that suits your needs.

## Additional Resources

For more information on using the Angular CLI, including detailed command references, visit the [Angular CLI Overview and Command Reference](https://angular.dev/tools/cli) page.
