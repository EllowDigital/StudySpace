import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { CheckCircle2, Download, FolderArchive, Rocket, Shield, Sparkles } from "lucide-react";
import { Link } from "react-router-dom";
import { RELEASE_DATE, RELEASE_VERSION_DISPLAY } from "@/lib/release-info";

const installSteps = [
  {
    title: "Download",
    detail: "Grab the Polaris build package with one click.",
    icon: Download,
  },
  {
    title: "Unzip",
    detail: "Extract anywhere—TypeBlitz stays portable.",
    icon: FolderArchive,
  },
  {
    title: "Launch",
    detail: "Run TypeBlitz.exe and sign in to sync streaks.",
    icon: Rocket,
  },
  {
    title: "Trust",
    detail: "Allow SmartScreen once; the binary is verified.",
    icon: Shield,
  },
];

const drills = [
  {
    title: "Warm-up",
    copy: "3 × 60s word sprints focusing on accuracy > 95%.",
  },
  {
    title: "Focus",
    copy: "Paragraph flow mode with pacing assistant enabled.",
  },
  {
    title: "Analytics",
    copy: "Review telemetry and tag friction keys.",
  },
];

const Quickstart = () => {
  return (
    <div className="section-padding">
      <div className="container px-4 md:px-6 space-y-16">
        <header className="text-center space-y-4">
          <Badge className="border-white/20 bg-white/5 text-white">Polaris build</Badge>
          <h1 className="text-4xl md:text-6xl font-bold text-white">Quickstart Guide</h1>
          <p className="text-lg text-muted-foreground max-w-3xl mx-auto">
            Ready in under two minutes. Follow this path to install TypeBlitz {RELEASE_VERSION_DISPLAY}
            , import your drills, and start logging streaks.
          </p>
          <div className="flex flex-wrap justify-center gap-3 text-sm text-white/70">
            <span className="rounded-full border border-white/15 px-4 py-1">Released {RELEASE_DATE}</span>
            <span className="rounded-full border border-white/15 px-4 py-1">Windows 10+</span>
            <span className="rounded-full border border-white/15 px-4 py-1">Offline friendly</span>
          </div>
          <div className="flex flex-wrap justify-center gap-4 pt-4">
            <Button asChild className="bg-neon text-black hover:bg-neon/90">
              <a href="#install">Jump to install</a>
            </Button>
            <Button variant="outline" className="border-white/20 text-white hover:bg-white/5" asChild>
              <Link to="/">Go back home</Link>
            </Button>
          </div>
        </header>

        <section id="install" className="grid gap-8 lg:grid-cols-2">
          <div className="rounded-3xl border border-white/10 bg-black/50 p-6 space-y-6">
            <h2 className="text-2xl font-semibold text-white">Installation checklist</h2>
            <p className="text-sm text-white/70">Follow the four steps and you are flight-ready.</p>
            <ol className="space-y-4">
              {installSteps.map((step, index) => {
                const Icon = step.icon;
                return (
                  <li key={step.title} className="flex gap-4 rounded-2xl border border-white/5 bg-white/0 p-4">
                    <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-neon/10 text-neon font-semibold">
                      {index + 1}
                    </div>
                    <div>
                      <div className="flex items-center gap-2 text-white">
                        <Icon className="h-4 w-4 text-neon" />
                        <p className="text-lg font-semibold">{step.title}</p>
                      </div>
                      <p className="text-sm text-white/70">{step.detail}</p>
                    </div>
                  </li>
                );
              })}
            </ol>
          </div>

          <div className="rounded-3xl border border-white/10 bg-black/50 p-6 space-y-6">
            <h2 className="text-2xl font-semibold text-white">First drill flow</h2>
            <p className="text-sm text-white/70">Use this cadence to calibrate TypeBlitz to your hands.</p>
            <div className="space-y-4">
              {drills.map((drill) => (
                <div key={drill.title} className="rounded-2xl border border-white/5 bg-white/0 p-4">
                  <p className="text-lg font-semibold text-white">{drill.title}</p>
                  <p className="text-sm text-white/70">{drill.copy}</p>
                </div>
              ))}
            </div>
            <div className="rounded-2xl border border-neon/50 bg-black/70 p-4 text-sm text-white shadow-[0_0_25px_rgba(58,255,194,0.25)]">
              <p className="font-semibold text-neon">Pro tip</p>
              <p className="text-white/80">
                Enable ghost input prediction to highlight hesitation points in real time.
              </p>
            </div>
          </div>
        </section>

        <section className="rounded-3xl border border-white/10 bg-gradient-to-br from-white/5 to-black/20 p-6 md:p-10 space-y-6">
          <div className="flex items-center gap-3 text-neon">
            <CheckCircle2 className="h-5 w-5" />
            <p className="uppercase text-xs tracking-[0.5em] text-white/70">Prerequisites</p>
          </div>
          <div className="grid gap-4 md:grid-cols-3">
            {[
              "Windows 10/11 with 2 GB RAM",
              "2 GB free storage",
              ".NET Desktop Runtime 8.0",
            ].map((item) => (
              <div key={item} className="rounded-2xl border border-white/5 bg-black/40 p-4 text-white/80">
                {item}
              </div>
            ))}
          </div>
          <p className="text-sm text-white/60">
            Optional: sign into your TypeBlitz profile to sync streaks, macros, and device settings. Offline
            mode works without sign-in.
          </p>
        </section>

        <section className="rounded-3xl border border-white/10 bg-black/40 p-6 space-y-6">
          <div className="flex items-center gap-3">
            <Sparkles className="h-5 w-5 text-neon" />
            <p className="text-white font-semibold">Need personalized help?</p>
          </div>
          <p className="text-white/70">
            Reach out via the contact form for migration assistance, enterprise rollouts, or classroom setups.
          </p>
          <div className="flex flex-wrap gap-4">
            <Button asChild>
              <Link to="/#contact">Contact support</Link>
            </Button>
            <Button variant="outline" asChild className="border-white/20 text-white hover:bg-white/5">
              <Link to="/">Back to landing</Link>
            </Button>
          </div>
        </section>
      </div>
    </div>
  );
};

export default Quickstart;
