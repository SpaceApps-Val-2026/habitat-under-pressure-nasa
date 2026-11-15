import { lazy, Suspense, useEffect, useRef, type ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import { I18nProvider, useI18n } from "./i18n";
import { AppBar } from "./layout/AppBar";
import { Footer } from "./layout/Footer";
import { Library } from "./screens/Library";
import { Home } from "./screens/Home";
import { Plan } from "./screens/Plan";
import { Run } from "./screens/Run";
import { ThemeProvider } from "./theme";
import { UnitsProvider } from "./units";

import "./ui/motion.css";
import "./ui/scroll.css";

// The panel and the report carry MapLibre and Observable Plot. Loading them on demand keeps
// the cover's first chunk free of the map.
const Panel = lazy(() => import("./screens/Panel").then((module) => ({ default: module.Panel })));
const Report = lazy(() => import("./screens/Report").then((module) => ({ default: module.Report })));
// The two editorial pages are read once; they stay out of the cover's chunk.
const About = lazy(() => import("./screens/About").then((module) => ({ default: module.About })));
const QuestionArticle = lazy(() =>
  import("./screens/QuestionArticle").then((module) => ({ default: module.QuestionArticle })),
);
const Manifesto = lazy(() => import("./screens/Manifesto").then((module) => ({ default: module.Manifesto })));

function SkipLink() {
  const { t } = useI18n();
  return (
    <a
      href="#main"
      className="sr-only focus:not-sr-only focus:fixed focus:left-16 focus:top-16 focus:z-50 focus:rounded-sm focus:bg-paper focus:px-12 focus:py-8 focus:text-ink focus:shadow-lg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus"
    >
      {t("nav.skipToContent")}
    </a>
  );
}

// The analysis an address belongs to: its story, its other formats and its data tab are one page family.
function analysisOf(pathname: string): string | null {
  return /^\/a\/([^/]+)/.exec(pathname)?.[1] ?? null;
}

function RouteFocus() {
  const { pathname } = useLocation();
  const previous = useRef<string | null>(null);
  useEffect(() => {
    const before = previous.current;
    previous.current = pathname;
    // Switching tabs of the same analysis keeps the reader where they were: no focus jump, no scroll.
    const same = before !== null && analysisOf(before) !== null && analysisOf(before) === analysisOf(pathname);
    if (same) return;
    // A new page starts at its top, header included: focusing the wrapper must not scroll to it, and the
    // scroll of the page the reader came from must not carry over.
    document.getElementById("main")?.focus({ preventScroll: true });
    if (before !== null) window.scrollTo(0, 0);
  }, [pathname]);
  return null;
}

// Each page fades in; the tabs of one analysis share a key, so switching between them does not replay it.
function Page({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  return (
    <div key={analysisOf(pathname) ?? pathname} className="fade-in">
      {children}
    </div>
  );
}

export function App() {
  return (
    <I18nProvider>
      <ThemeProvider>
        <UnitsProvider>
          <div className="body flex min-h-screen flex-col bg-fog text-ink">
            <SkipLink />
            <RouteFocus />
            <AppBar />
            {/* The skip target is the wrapper, not a second landmark: each screen brings its own <main>. */}
            <div id="main" tabIndex={-1} className="flex-1 outline-none">
              <Suspense fallback={null}>
                <Page>
                <Routes>
                  <Route path="/" element={<Home />} />
                  <Route path="/plan" element={<Plan />} />
                  <Route path="/run/:analysisId" element={<Run />} />
                  {/* The story is the default view of an analysis; the data panel is one of its tabs. */}
                  <Route path="/a/:analysisId" element={<Report />} />
                  <Route path="/a/:analysisId/panel" element={<Panel />} />
                  <Route path="/a/:analysisId/report/:style" element={<Report />} />
                  <Route path="/library" element={<Library />} />
                  <Route path="/about" element={<About />} />
                  <Route path="/manifesto" element={<Manifesto />} />
                  <Route path="/q/:id" element={<QuestionArticle />} />
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
                </Page>
              </Suspense>
            </div>
            <Footer />
          </div>
        </UnitsProvider>
      </ThemeProvider>
    </I18nProvider>
  );
}
