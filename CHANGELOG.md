# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Angular chat UI in the style of ChatGPT: a left history panel and a main chat panel, with text
  messages, image attachments, and a light/dark theme toggle that persists per browser.
- Chat history and theme preference persisted to the browser's local storage.
- FastAPI backend: chat replies stream to the UI over Server-Sent Events instead of arriving as
  one blocking response, and a separate endpoint handles image uploads for chat attachments.
- A mocked chat responder is kept as a fallback/test double; the UI talks to the real backend by
  default.
