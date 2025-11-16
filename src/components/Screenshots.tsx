import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { MonitorSmartphone, MoveLeft, MoveRight } from "lucide-react";

const screenshotData = [
  {
    title: "Mission control dashboard",
    description: "Widgets for WPM, accuracy, goal streaks, and focus minutes in one view.",
    image: "/assets/images/screenshots/dashboard.webp",
    stat: "WPM+48%",
  },
  {
    title: "Word sprint drills",
    description: "Adaptive word packs spotlight weak key clusters and pacing dips.",
    image: "/assets/images/screenshots/practice-words.webp",
    stat: "Auto coach",
  },
  {
    title: "Paragraph flow mode",
    description: "Narrative passages train rhythm, punctuation, and long-form accuracy.",
    image: "/assets/images/screenshots/practice-paragraph.webp",
    stat: "Flow+",
  },
  {
    title: "Performance telemetry",
    description: "Charts with per-key heatmaps, fatigue predictions, and trend tags.",
    image: "/assets/images/screenshots/performance-history.webp",
    stat: "Telemetry",
  },
  {
    title: "Update concierge",
    description: "Background updater checks builds, verifies hashes, and installs silently.",
    image: "/assets/images/screenshots/check-update.webp",
    stat: "AutoPatch",
  },
  {
    title: "Patch timeline",
    description: "See changelog, download size, and install ETA inside the app.",
    image: "/assets/images/screenshots/update-status.webp",
    stat: "Timeline",
  },
];

const Screenshots = () => {
  const [activeIndex, setActiveIndex] = useState(0);
  const sectionRef = useRef<HTMLElement>(null);
  const autoAdvanceRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const [entry] = entries;
        if (entry.isIntersecting) entry.target.classList.add("animate-fade-in");
      },
      { threshold: 0.15 }
    );

    if (sectionRef.current) observer.observe(sectionRef.current);

    return () => {
      if (sectionRef.current) observer.unobserve(sectionRef.current);
    };
  }, []);

  useEffect(() => {
    autoAdvanceRef.current && clearInterval(autoAdvanceRef.current);
    autoAdvanceRef.current = setInterval(() => {
      setActiveIndex((prev) => (prev === screenshotData.length - 1 ? 0 : prev + 1));
    }, 6000);

    return () => {
      autoAdvanceRef.current && clearInterval(autoAdvanceRef.current);
    };
  }, []);

  const handlePrev = () => {
    setActiveIndex((prev) => (prev === 0 ? screenshotData.length - 1 : prev - 1));
  };

  const handleNext = () => {
    setActiveIndex((prev) => (prev === screenshotData.length - 1 ? 0 : prev + 1));
  };

  return (
    <section id="screenshots" ref={sectionRef} className="section-padding relative opacity-0">
      <div className="absolute inset-0 -z-10">
        <div className="absolute top-10 right-0 h-72 w-72 rounded-full bg-neon/15 blur-[160px]" />
      </div>

      <div className="container px-4 md:px-6">
        <div className="flex flex-col items-center gap-4 text-center">
          <Badge className="border-white/20 bg-white/5 text-white">
            <MonitorSmartphone className="mr-2 h-4 w-4" />Live preview
          </Badge>
          <h2 className="section-title">Interface sneak peek</h2>
          <p className="section-subtitle max-w-3xl">
            Swipe through the latest Polaris visuals—optimized for windowed, ultra-wide, and travel screens alike.
          </p>
        </div>

        <div className="mt-14 grid gap-10 lg:grid-cols-[1fr_0.6fr]">
          <div className="relative overflow-hidden rounded-[32px] border border-white/10 bg-gradient-to-br from-black/60 to-black/20 p-1">
            <div className="relative aspect-[16/9] overflow-hidden rounded-[28px]">
              <img
                key={screenshotData[activeIndex].image}
                src={screenshotData[activeIndex].image}
                alt={screenshotData[activeIndex].title}
                className="h-full w-full object-cover transition duration-500"
              />
              <div className="absolute inset-x-6 bottom-6 rounded-2xl border border-white/10 bg-black/70 p-5">
                <div className="flex items-center justify-between gap-4">
                  <div>
                    <p className="text-sm uppercase tracking-[0.4em] text-muted-foreground">{screenshotData[activeIndex].stat}</p>
                    <h3 className="text-2xl font-semibold text-white">{screenshotData[activeIndex].title}</h3>
                    <p className="text-sm text-white/70">{screenshotData[activeIndex].description}</p>
                  </div>
                  <div className="hidden sm:flex flex-col items-end text-right text-xs text-white/60">
                    <span>Slide {activeIndex + 1}</span>
                    <span>of {screenshotData.length}</span>
                  </div>
                </div>
              </div>
            </div>

            <div className="pointer-events-none absolute inset-0 rounded-[32px] border border-white/5" aria-hidden />

            <div className="absolute inset-y-0 left-0 flex items-center">
              <button
                onClick={handlePrev}
                className="pointer-events-auto m-4 flex h-12 w-12 items-center justify-center rounded-full bg-black/60 text-white shadow-lg transition hover:bg-black/80"
                aria-label="Previous screenshot"
              >
                <MoveLeft className="h-5 w-5" />
              </button>
            </div>
            <div className="absolute inset-y-0 right-0 flex items-center">
              <button
                onClick={handleNext}
                className="pointer-events-auto m-4 flex h-12 w-12 items-center justify-center rounded-full bg-black/60 text-white shadow-lg transition hover:bg-black/80"
                aria-label="Next screenshot"
              >
                <MoveRight className="h-5 w-5" />
              </button>
            </div>
          </div>

          <div className="glass rounded-3xl border border-white/10 p-6">
            <div className="mb-4 flex items-center justify-between text-sm text-muted-foreground">
              <span>Slides lineup</span>
              <span>{activeIndex + 1}/{screenshotData.length}</span>
            </div>
            <div className="space-y-4">
              {screenshotData.map((slide, index) => (
                <button
                  key={slide.title}
                  onClick={() => setActiveIndex(index)}
                  className={`w-full rounded-2xl border p-4 text-left transition ${
                    activeIndex === index
                      ? "border-neon/60 bg-neon/10"
                      : "border-white/5 bg-white/0 hover:border-white/15"
                  }`}
                >
                  <p className="text-xs uppercase tracking-[0.4em] text-muted-foreground">{slide.stat}</p>
                  <p className="mt-1 text-lg font-semibold text-white">{slide.title}</p>
                  <p className="text-sm text-white/70">{slide.description}</p>
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
          {screenshotData.map((_, index) => (
            <span
              key={`dot-${index}`}
              className={`h-2 w-10 rounded-full transition ${
                index === activeIndex ? "bg-neon" : "bg-white/15"
              }`}
            />
          ))}
        </div>
      </div>
    </section>
  );
};

export default Screenshots;
