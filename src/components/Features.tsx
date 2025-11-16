import { useEffect, useRef } from "react";
import { Badge } from "@/components/ui/badge";
import { Activity, GaugeCircle, Keyboard, Sparkles, Trophy, Zap } from "lucide-react";

const flagshipFeatures = [
  {
    icon: Keyboard,
    title: "Adaptive drill engine",
    copy: "Every run adjusts duration, difficulty, and vocabulary based on your live accuracy and rhythm.",
    tags: ["real-time", "AI tuned"],
  },
  {
    icon: GaugeCircle,
    title: "Focus dashboard",
    copy: "Minimal HUD surfaces WPM, accuracy, cadence, and muscle-memory streaks without distraction.",
    tags: ["HUD", "customizable"],
  },
  {
    icon: Activity,
    title: "Telemetry-grade analytics",
    copy: "Dive into per-key heatmaps, error clusters, and fatigue predictions to coach smarter sessions.",
    tags: ["heatmaps", "insights"],
  },
];

const proBoosters = [
  {
    title: "Cloud backup optionality",
    description: "Sync runs when online, keep everything local when you're not.",
    stat: "256-bit",
  },
  {
    title: "Macro-safe sandbox",
    description: "Detection prevents harmful scripts from hijacking your drills.",
    stat: "SafeGuard",
  },
  {
    title: "Performance friendly",
    description: "Uses <180MB RAM on average—even during analytics crunching.",
    stat: "<180MB",
  },
  {
    title: "Rapid updates",
    description: "Signed nightly builds for testers, stable builds for everyone else.",
    stat: "Weekly",
  },
];

const Features = () => {
  const cardRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) entry.target.classList.add("animate-fade-in");
        });
      },
      { threshold: 0.15 }
    );

    cardRefs.current.forEach((ref) => ref && observer.observe(ref));

    return () => {
      cardRefs.current.forEach((ref) => ref && observer.unobserve(ref));
    };
  }, []);

  return (
    <section id="features" className="section-padding relative overflow-hidden">
      <div className="absolute inset-0 -z-10">
        <div className="absolute top-0 left-1/3 h-96 w-96 -translate-x-1/2 rounded-full bg-neon/10 blur-[140px]" />
        <div className="absolute bottom-0 right-0 h-[28rem] w-[28rem] translate-x-1/3 rounded-full bg-[#3b82f672]/40 blur-[180px]" />
      </div>

      <div className="container px-4 md:px-6">
        <div className="flex flex-col items-center gap-6 text-center">
          <Badge className="border-white/20 bg-white/5 text-white">
            <Sparkles className="mr-2 h-4 w-4" />Power features
          </Badge>
          <h2 className="section-title">Built for serious typists</h2>
          <p className="section-subtitle max-w-3xl">
            TypeBlitz pairs a razor-sharp UI with telemetry-driven coaching so you can push past plateaus
            without burning out.
          </p>
        </div>

        <div className="mt-14 grid gap-6 lg:grid-cols-[0.7fr_0.3fr]">
          <div className="grid gap-6 md:grid-cols-2">
            {flagshipFeatures.map((feature, index) => {
              const Icon = feature.icon;
              return (
                <div
                  key={feature.title}
                  ref={(el) => (cardRefs.current[index] = el)}
                  className="opacity-0 rounded-3xl border border-white/10 bg-black/50 p-6 text-left shadow-[0_20px_80px_rgba(0,0,0,0.35)]"
                  style={{ animationDelay: `${index * 120}ms` }}
                >
                  <div className="mb-5 flex h-14 w-14 items-center justify-center rounded-2xl bg-neon/15 text-neon">
                    <Icon className="h-6 w-6" />
                  </div>
                  <h3 className="text-2xl font-semibold text-white">{feature.title}</h3>
                  <p className="mt-3 text-base text-muted-foreground">{feature.copy}</p>
                  <div className="mt-5 flex flex-wrap gap-2">
                    {feature.tags.map((tag) => (
                      <Badge key={tag} variant="outline" className="border-white/20 text-white/80">
                        {tag}
                      </Badge>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="rounded-3xl border border-white/10 bg-gradient-to-b from-white/5 to-black/40 p-6">
            <div className="mb-6 flex items-center gap-3">
              <Trophy className="h-5 w-5 text-neon" />
              <p className="text-sm uppercase tracking-[0.5em] text-muted-foreground">Rank up faster</p>
            </div>
            <p className="text-4xl font-semibold text-white">48%</p>
            <p className="text-sm text-muted-foreground">Average accuracy gain after 10 focused drills.</p>
            <div className="mt-6 space-y-4">
              {proBoosters.map((booster, boosterIndex) => (
                <div
                  key={booster.title}
                  ref={(el) => (cardRefs.current[flagshipFeatures.length + boosterIndex] = el)}
                  className="rounded-2xl border border-white/5 bg-black/30 p-4 opacity-0"
                  style={{ animationDelay: `${(flagshipFeatures.length + boosterIndex) * 120}ms` }}
                >
                  <div className="flex items-center justify-between">
                    <h4 className="text-base font-semibold text-white">{booster.title}</h4>
                    <span className="text-xs text-neon uppercase tracking-[0.2em]">{booster.stat}</span>
                  </div>
                  <p className="mt-2 text-sm text-white/70">{booster.description}</p>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="mt-10 flex flex-wrap items-center justify-between gap-4 rounded-3xl border border-white/5 bg-black/40 px-6 py-5 text-sm text-muted-foreground">
          <div className="flex items-center gap-2 text-white">
            <Zap className="h-4 w-4 text-neon" />
            Ghost input prediction reduces error latency in real time.
          </div>
          <div className="flex items-center gap-2 text-white">
            <Sparkles className="h-4 w-4 text-neon" />
            Shortcut coach quietly tracks macros per device.
          </div>
        </div>
      </div>
    </section>
  );
};

export default Features;
