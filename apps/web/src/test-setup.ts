import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom has no 2D canvas and logs a "Not implemented" error on every getContext call.
// Components that draw must fall back; returning null keeps the test output clean.
HTMLCanvasElement.prototype.getContext = (() => null) as typeof HTMLCanvasElement.prototype.getContext;

afterEach(cleanup);
