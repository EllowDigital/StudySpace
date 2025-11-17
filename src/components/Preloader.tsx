import { useEffect, useMemo, useState } from "react";

const loadingStages = [
  {
    threshold: 30,
    headline: "Calibrating keymaps",
    description: "Matching shortcuts with your typing profile.",
  },
  {
    threshold: 55,
    headline: "Spinning up drills",
    description: "Tuning adaptive challenges for laser-focused practice.",
  },
  {
    threshold: 80,
    headline: "Syncing achievements",
    description: "Keeping your milestones and streaks up to date.",
  },
  {
    threshold: 101,
    headline: "Preparing HUD",
    description: "Final polish before launching the dashboard.",
  },
];

const Preloader = () => {
  const [isLoading, setIsLoading] = useState(true);
  const [progress, setProgress] = useState(0);

  useEffect(() => {
    const timer = setTimeout(() => {
      setIsLoading(false);
    }, 3000);

    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (!isLoading) return;

    const interval = setInterval(() => {
      setProgress((prev) => {
        if (prev >= 100) {
          clearInterval(interval);
          return 100;
        }

        const increment = Math.random() * 12 + 6; // keep motion varied
        return Math.min(prev + increment, 100);
      });
    }, 180);

    return () => clearInterval(interval);
  }, [isLoading]);

  const activeStage = useMemo(() => {
    return (
      loadingStages.find((stage) => progress < stage.threshold) ??
      loadingStages[loadingStages.length - 1]
    );
  }, [progress]);

  if (!isLoading) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-hidden bg-gradient-to-br from-black via-dark to-black text-white">
      {/* Ambient glow */}
      <div className="absolute inset-0">
        <div className="absolute -top-4 -right-6 h-56 w-56 rounded-full bg-neon/25 blur-3xl opacity-50" />
        <div className="absolute -bottom-8 -left-6 h-64 w-64 rounded-full bg-white/5 blur-[140px]" />
      </div>

      <div className="relative w-full max-w-2xl px-4 sm:px-6">
        <div className="glass border border-white/10 rounded-3xl p-6 sm:p-8 backdrop-blur-xl shadow-[0_0_60px_rgba(88,112,172,0.15)]">
          <div className="flex flex-col items-center gap-6 text-center">
            <div className="space-y-2">
              <p className="text-xs uppercase tracking-[0.5em] text-neon">TypeBlitz</p>
              <h2 className="text-2xl font-semibold sm:text-3xl">
                Preparing precision typing arena
              </h2>
              <p className="text-sm sm:text-base text-muted-foreground">
                {activeStage.description}
              </p>
            </div>

            <div className="flex w-full flex-col gap-6 md:flex-row md:items-center">
              <div className="mx-auto flex h-28 w-28 items-center justify-center rounded-full bg-black/40 p-2 shadow-inner">
                <div
                  className="relative flex h-full w-full items-center justify-center rounded-full bg-black/70"
                  style={{
                    background: `conic-gradient(#5870ac ${progress * 3.6}deg, rgba(255,255,255,0.07) ${progress * 3.6}deg)`,
                  }}
                >
                  <div className="flex h-[82%] w-[82%] flex-col items-center justify-center rounded-full bg-black/80">
                    <span className="text-2xl font-bold">{Math.round(progress)}</span>
                    <span className="text-[10px] uppercase tracking-widest text-muted-foreground">
                      %
                    </span>
                  </div>
                  <span className="sr-only" role="status" aria-live="polite">
                    {`Loading ${Math.round(progress)} percent`}
                  </span>
                </div>
              </div>

              <div className="flex-1 space-y-4">
                <div className="flex items-center justify-between text-sm text-muted-foreground">
                  <span>{activeStage.headline}</span>
                  <span>{Math.round(progress)}%</span>
                </div>
                <div
                  className="h-1 w-full rounded-full bg-white/10"
                  role="progressbar"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={Math.round(progress)}
                >
                  <div
                    className="h-full rounded-full bg-neon shadow-[0_0_20px_rgba(88,112,172,0.6)] transition-all duration-200"
                    style={{ width: `${progress}%` }}
                  />
                </div>
                <p className="text-sm text-muted-foreground">
                  Optimizing experience for <span className="text-white">keyboard athletes</span>{" "}
                  worldwide.
                </p>
              </div>
            </div>

            <div className="grid w-full gap-3 text-left sm:grid-cols-2">
              {loadingStages.map((stage, index) => {
                const isCompleted = progress >= stage.threshold;
                const isActive = !isCompleted && stage === activeStage;

                return (
                  <div
                    key={stage.headline}
                    className={`rounded-2xl border px-3 py-3 transition-all duration-300 ${
                      isCompleted
                        ? "border-neon/40 bg-neon/10 text-white"
                        : isActive
                          ? "border-white/30 bg-white/5 text-white"
                          : "border-white/5 bg-white/0 text-white/60"
                    }`}
                  >
                    <p className="text-[10px] uppercase tracking-[0.3em] text-muted-foreground">
                      Stage {index + 1}
                    </p>
                    <p className="text-base font-semibold">{stage.headline}</p>
                    <p className="text-sm text-muted-foreground">{stage.description}</p>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Preloader;
