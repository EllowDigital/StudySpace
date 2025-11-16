import { useEffect, useRef, useState } from "react";
import Preloader from "@/components/Preloader";
import Navbar from "@/components/Navbar";
import Hero from "@/components/Hero";
import About from "@/components/About";
import Features from "@/components/Features";
import Screenshots from "@/components/Screenshots";
import Download from "@/components/Download";
import Installation from "@/components/Installation";
import Contact from "@/components/Contact";
import Footer from "@/components/Footer";
import ScrollToTop from "@/components/ScrollToTop";
import { useToast } from "@/hooks/use-toast";
import { RELEASE_DATE, RELEASE_VERSION_DISPLAY } from "@/lib/release-info";

const announcementSections = [
  { id: "features", label: "Power Features" },
  { id: "screenshots", label: "Screenshots" },
  { id: "installation", label: "Installation" },
  { id: "download", label: "Downloads" },
  { id: "contact", label: "Contact" },
];

const Index = () => {
  const [isLoaded, setIsLoaded] = useState(false);
  const observersRef = useRef<IntersectionObserver[]>([]);
  const triggeredSectionsRef = useRef(new Set<string>());
  const { toast } = useToast();

  useEffect(() => {
    // Simulate initial loading time for the preloader to be shown
    const timer = setTimeout(() => {
      setIsLoaded(true);
    }, 3000);

    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const previousRestoration = "scrollRestoration" in history ? history.scrollRestoration : null;
    if ("scrollRestoration" in history) {
      history.scrollRestoration = "manual";
    }

    const snapToHero = () => {
      const heroEl = document.getElementById("hero");
      if (heroEl) {
        heroEl.scrollIntoView({ behavior: "auto", block: "start" });
      } else {
        window.scrollTo({ top: 0, behavior: "auto" });
      }
    };

    requestAnimationFrame(snapToHero);

    return () => {
      if (previousRestoration !== null && "scrollRestoration" in history) {
        history.scrollRestoration = previousRestoration;
      }
    };
  }, []);

  useEffect(() => {
    if (!isLoaded || typeof window === "undefined") return;

    observersRef.current.forEach((observer) => observer.disconnect());
    observersRef.current = [];

    announcementSections.forEach((section) => {
      const target = document.getElementById(section.id);
      if (!target) return;

      const observer = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting && !triggeredSectionsRef.current.has(section.id)) {
            triggeredSectionsRef.current.add(section.id);
            toast({
              title: `${RELEASE_VERSION_DISPLAY} ready`,
              description: `${section.label} refreshed • ${RELEASE_DATE}`,
              duration: 4500,
            });
            observer.unobserve(entry.target);
          }
        },
        { threshold: 0.45 }
      );

      observer.observe(target);
      observersRef.current.push(observer);
    });

    return () => {
      observersRef.current.forEach((observer) => observer.disconnect());
      observersRef.current = [];
    };
  }, [toast, isLoaded]);

  return (
    <>
      <Preloader />

      <div className={`transition-opacity duration-700 ${isLoaded ? "opacity-100" : "opacity-0"}`}>
        <Navbar />
        <main>
          <Hero />
          <About />
          <Features />
          <Screenshots />
          <Download />
          <Installation />
          <Contact />
        </main>
        <Footer />
        <ScrollToTop />
      </div>
    </>
  );
};

export default Index;
