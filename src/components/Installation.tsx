import { useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import {
  ArrowRight,
  CheckCircle2,
  Download,
  FolderArchive,
  Laptop2,
  ShieldCheck,
  Sparkles,
  TimerReset,
} from "lucide-react";
import { RELEASE_DATE, RELEASE_VERSION_DISPLAY } from "@/lib/release-info";

const installSteps = [
  {
    title: "Download the Polaris build",
    detail: "Grab the latest TypeBlitz package straight from the secure CDN.",
    icon: Download,
    eta: "20s",
  },
  {
    title: "Unzip anywhere",
    detail: "Extract the folder to Documents, Desktop, or your favorite tools drive.",
    icon: FolderArchive,
    eta: "15s",
  },
  {
    title: "Launch TypeBlitz.exe",
    detail: "The dashboard boots instantly with your saved preferences.",
    icon: Laptop2,
    eta: "10s",
  },
  {
    title: "Allow Windows SmartScreen",
    detail: 'Tap "More info" → "Run anyway" to trust the unsigned build.',
    icon: ShieldCheck,
    eta: "5s",
  },
  {
    title: "Sync drills & shortcuts",
    detail: "Final handshake pulls presets, achievements, and streak data.",
    icon: Sparkles,
    eta: "Instant",
  },
];

const quickNotes = [
  {
    title: "Zero bloat install",
    detail: "Portable build, no registry edits, uninstall by deleting the folder.",
  },
  {
    title: "Offline ready",
    detail: "Runs without internet. Connect later to sync leaderboards.",
  },
  {
    title: "Safe & transparent",
    detail: "Checksum verified; SmartScreen prompt only due to unsigned certificate.",
  },
];

const Installation = () => {
  const sectionRef = useRef<HTMLElement>(null);

  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("animate-fade-in");
        }
      },
      { threshold: 0.15 }
    );

    const currentRef = sectionRef.current;
    if (currentRef) observer.observe(currentRef);

    return () => {
      if (currentRef) observer.unobserve(currentRef);
    };
  }, []);

  return (
    <section
      id="installation"
      ref={sectionRef}
      className="section-padding relative opacity-0 overflow-hidden"
    >
      <div className="absolute inset-0 -z-10">
        <div className="absolute -top-24 right-10 h-56 w-56 rounded-full bg-neon/20 blur-[120px]" />
        <div className="absolute bottom-0 left-0 h-72 w-72 rounded-full bg-white/5 blur-[140px]" />
      </div>

      <div className="container px-4 md:px-6">
        <div className="text-center mb-16">
          <p className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-black/30 px-4 py-1 text-xs uppercase tracking-[0.3em] text-muted-foreground">
            <TimerReset className="h-3.5 w-3.5" />
            Setup ETA: 2 minutes
          </p>
          <h2 className="section-title">Installation Flight Plan</h2>
          <p className="section-subtitle">
            Designed to be portable, predictable, and resilient—whether you're installing on your
            main rig or a travel laptop.
          </p>
        </div>

        <div className="grid gap-10 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
          <div className="space-y-6">
            <article className="glass border-white/10 rounded-3xl p-8 shadow-[0_20px_80px_rgba(0,0,0,0.35)]">
              <div className="flex flex-col gap-6">
                <div>
                  <p className="text-sm uppercase tracking-[0.4em] text-muted-foreground">Release</p>
                  <h3 className="text-3xl font-semibold text-white">{RELEASE_VERSION_DISPLAY}</h3>
                  <p className="text-muted-foreground">Rolled out on {RELEASE_DATE}</p>
                </div>

                <div className="rounded-2xl bg-black/40 border border-white/5 p-5">
                  <p className="text-sm text-muted-foreground">Focus upgrades</p>
                  <ul className="mt-3 space-y-2 text-base">
                    <li className="flex items-center gap-2">
                      <CheckCircle2 className="h-4 w-4 text-neon" />Adaptive drills auto-tune to
                      your pacing
                    </li>
                    <li className="flex items-center gap-2">
                      <CheckCircle2 className="h-4 w-4 text-neon" />Progress HUD redesigned for
                      quick-glance stats
                    </li>
                    <li className="flex items-center gap-2">
                      <CheckCircle2 className="h-4 w-4 text-neon" />Performance lighter by 18%
                      on low-power CPUs
                    </li>
                  </ul>
                </div>

                <div className="flex flex-col gap-3 sm:flex-row">
                  <Button className="flex-1 bg-neon text-dark hover:bg-neon/90" asChild>
                    <a href="#download">
                      Download package
                      <ArrowRight className="ml-2 h-4 w-4" />
                    </a>
                  </Button>
                  <Button
                    variant="outline"
                    className="flex-1 border-white/20 text-white hover:bg-white/5"
                    onClick={() => window.open("https://typeblitz.tech/docs", "_blank")}
                  >
                    View quickstart
                  </Button>
                </div>
              </div>
            </article>

            <div className="grid gap-4 md:grid-cols-2">
              {quickNotes.map((note) => (
                <div
                  key={note.title}
                  className="rounded-2xl border border-white/10 bg-black/30 p-4 text-left"
                >
                  <p className="text-sm uppercase tracking-[0.3em] text-muted-foreground">{note.title}</p>
                  <p className="mt-2 text-base text-white/80">{note.detail}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="glass rounded-3xl border-white/10 p-6 lg:p-8">
            <ol className="relative space-y-6">
              {installSteps.map((step, index) => {
                const Icon = step.icon;
                return (
                  <li key={step.title} className="relative pl-12">
                    {index !== installSteps.length - 1 && (
                      <span className="absolute left-[21px] top-8 h-full w-px bg-white/10" aria-hidden />
                    )}
                    <span className="absolute left-0 top-0 flex h-10 w-10 items-center justify-center rounded-xl border border-neon/40 bg-black/60 text-neon">
                      <Icon className="h-4 w-4" />
                    </span>
                    <div className="flex flex-col gap-2 rounded-2xl border border-white/5 bg-black/40 p-4">
                      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                        <p className="text-sm text-muted-foreground uppercase tracking-[0.2em]">
                          Step {index + 1}
                        </p>
                        <span className="text-xs text-white/70">~ {step.eta}</span>
                      </div>
                      <h3 className="text-lg font-semibold">{step.title}</h3>
                      <p className="text-sm text-muted-foreground">{step.detail}</p>
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>
        </div>
      </div>
    </section>
  );
};

export default Installation;
