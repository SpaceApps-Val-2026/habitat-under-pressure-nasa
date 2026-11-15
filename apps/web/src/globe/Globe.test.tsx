import { render } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

import { Globe } from "./Globe";

const CONTAINER = { width: 390, height: 232 };

function fakeContext() {
  const points: number[] = [];
  const gradient = { addColorStop: vi.fn() };
  const record = (...values: number[]) => {
    points.push(...values);
  };
  const context = {
    fillStyle: "",
    strokeStyle: "",
    lineWidth: 1,
    globalAlpha: 1,
    lineJoin: "round" as CanvasLineJoin,
    clearRect: vi.fn(),
    fillRect: vi.fn(),
    beginPath: vi.fn(),
    closePath: vi.fn(),
    arc: vi.fn((x: number, y: number) => record(x, y)),
    rect: vi.fn((x: number, y: number) => record(x, y)),
    moveTo: vi.fn((x: number, y: number) => record(x, y)),
    lineTo: vi.fn((x: number, y: number) => record(x, y)),
    fill: vi.fn(),
    stroke: vi.fn(),
    setTransform: vi.fn(),
    createRadialGradient: vi.fn(() => gradient),
  };
  return { context, points };
}

beforeEach(() => {
  vi.spyOn(HTMLCanvasElement.prototype, "getBoundingClientRect").mockReturnValue({
    ...CONTAINER,
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: CONTAINER.width,
    bottom: CONTAINER.height,
    toJSON: () => ({}),
  } as DOMRect);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

test("draws the resting place on a horizon canvas and reports it once", () => {
  const { context, points } = fakeContext();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    () => context as unknown as CanvasRenderingContext2D,
  );
  const requestFrame = vi.fn();
  vi.stubGlobal("requestAnimationFrame", requestFrame);
  const onPlaceChange = vi.fn();

  const { container } = render(
    <Globe label="The Earth seen as a horizon" paused onPlaceChange={onPlaceChange} />,
  );

  const canvas = container.querySelector("canvas");
  expect(canvas).toHaveAttribute("role", "img");
  expect(canvas).toHaveAttribute("aria-label", "The Earth seen as a horizon");
  expect(context.clearRect).toHaveBeenCalled();
  expect(context.stroke).toHaveBeenCalled();
  expect(points.length).toBeGreaterThan(100);
  expect(points.every((value) => Number.isFinite(value))).toBe(true);
  expect(onPlaceChange).toHaveBeenCalledTimes(1);
  expect(onPlaceChange.mock.calls[0][0].id).toBe("sagunt");
  expect(requestFrame).not.toHaveBeenCalled();
});

test("honours initialId when motion is reduced", () => {
  const { context } = fakeContext();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    () => context as unknown as CanvasRenderingContext2D,
  );
  const requestFrame = vi.fn();
  vi.stubGlobal("requestAnimationFrame", requestFrame);
  const listeners: Array<() => void> = [];
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({
      matches: true,
      addEventListener: (_: string, callback: () => void) => listeners.push(callback),
      removeEventListener: vi.fn(),
    })),
  );
  const onPlaceChange = vi.fn();

  render(<Globe label="Still Earth" initialId="svalbard" onPlaceChange={onPlaceChange} />);

  expect(onPlaceChange).toHaveBeenCalledTimes(1);
  expect(onPlaceChange.mock.calls[0][0].id).toBe("svalbard");
  expect(requestFrame).not.toHaveBeenCalled();
});

test("falls back to the first place when initialId is unknown", () => {
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
  const onPlaceChange = vi.fn();

  render(<Globe label="No canvas" paused initialId="atlantis" onPlaceChange={onPlaceChange} />);

  expect(onPlaceChange).toHaveBeenCalledTimes(1);
  expect(onPlaceChange.mock.calls[0][0].id).toBe("svalbard");
});

test("the tour settles somewhere else without NaN coordinates", () => {
  const { context, points } = fakeContext();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(
    () => context as unknown as CanvasRenderingContext2D,
  );
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    frames.push(callback);
    return frames.length;
  });
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  const onPlaceChange = vi.fn();

  render(<Globe label="Moving Earth" onPlaceChange={onPlaceChange} />);
  expect(onPlaceChange).toHaveBeenCalledTimes(1);

  let now = 0;
  for (let i = 0; i < 250 && frames.length > 0; i++) {
    const frame = frames.shift();
    if (!frame) break;
    now += 16;
    frame(now);
  }

  expect(onPlaceChange.mock.calls.length).toBeGreaterThan(1);
  const settled = onPlaceChange.mock.calls.at(-1)?.[0];
  expect(settled.id).not.toBe("sagunt");
  expect(points.every((value) => Number.isFinite(value))).toBe(true);
});
