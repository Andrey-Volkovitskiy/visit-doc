# frontend

React + Vite streaming chat SPA for VisitDoc's Grounded FAQ Chat (`specs/001-grounded-faq-chat/`).
Plain Node project, **not** a `uv` workspace member — managed independently with `npm`.

## Stack

- React 19 + Vite 8, TypeScript
- Vitest + React Testing Library for tests

## Structure

```
src/
├── App.tsx                    # renders <ChatWindow />
├── main.tsx                   # React entry point
├── components/
│   └── ChatWindow.tsx         # chat input/button, streamed answer, citations
└── lib/
    └── chatStream.ts          # askChat(): POST /chat; parseNdjsonStream(): NDJSON parsing
tests/
├── ChatWindow.test.tsx
├── chatStream.test.ts
└── setup.ts
```

## Getting started

```bash
npm install
npm run dev      # Vite dev server, proxies /chat and /faq to http://localhost:8000
```

The dev server proxy (`vite.config.ts`) expects the `chat` service running locally on port 8000
(see the repo root `README.md` for how to start it, including `make db-up` for its Postgres/Qdrant
dependencies).

## Commands

```bash
npm run dev       # start Vite dev server
npm run build     # tsc -b && vite build -> dist/
npm run preview   # preview the production build locally
npm run test      # run the Vitest suite once
```

## Notes

- Streaming transport is NDJSON over a plain `fetch` + `ReadableStream` (not SSE/WebSocket) — see
  the "Grounded FAQ Chat: technology choices" section in the repo root `README.md` for the rationale.
- `dist/` (build output) and `node_modules/` are gitignored; `*.tsbuildinfo` files are gitignored too.
