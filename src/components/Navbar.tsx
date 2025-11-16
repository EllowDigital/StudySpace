import { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Download, Menu } from "lucide-react";
import { Link, useLocation } from "react-router-dom";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet";

const sectionLinks = [
  { id: "about", label: "About" },
  { id: "features", label: "Features" },
  { id: "screenshots", label: "Screenshots" },
  { id: "download", label: "Download" },
  { id: "contact", label: "Contact" },
];

const Navbar = () => {
  const [scrolled, setScrolled] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const location = useLocation();
  const isHomePage = location.pathname === "/";

  useEffect(() => {
    const handleScroll = () => {
      const isScrolled = window.scrollY > 20;
      if (isScrolled !== scrolled) {
        setScrolled(isScrolled);
      }
    };

    window.addEventListener("scroll", handleScroll);
    return () => window.removeEventListener("scroll", handleScroll);
  }, [scrolled]);

  const scrollToSection = (sectionId: string) => {
    if (isHomePage) {
      const section = document.getElementById(sectionId);
      if (section) {
        section.scrollIntoView({ behavior: "smooth" });
      }
    }
    setMobileMenuOpen(false);
  };

  return (
    <header
      className={`fixed top-0 left-0 right-0 z-40 transition-all duration-300 ${
        scrolled ? "glass py-3" : "bg-transparent py-5"
      }`}
    >
      <div className="container flex items-center justify-between">
        {/* Logo and Brand */}
        <div className="flex items-center space-x-2">
          <Link to="/" className="flex items-center space-x-2">
            <img
              src="/assets/images/TypeBlitz.png"
              alt="TypeBlitz Logo"
              className="w-9 h-9 object-contain rounded-full"
            />
            <span className="text-xl font-bold text-white">TypeBlitz</span>
          </Link>
        </div>

        {/* Desktop Navigation */}
        <nav className="hidden md:flex items-center gap-6">
          {sectionLinks.map((link) =>
            isHomePage ? (
              <button
                key={link.id}
                onClick={() => scrollToSection(link.id)}
                className="text-sm text-muted-foreground hover:text-white transition-colors"
              >
                {link.label}
              </button>
            ) : (
              <Link
                key={link.id}
                to={`/#${link.id}`}
                className="text-sm text-muted-foreground hover:text-white transition-colors"
              >
                {link.label}
              </Link>
            )
          )}
          <Link
            to="/quickstart"
            className="text-sm text-neon font-semibold hover:text-white transition-colors"
          >
            Quickstart
          </Link>
        </nav>

        {/* Desktop Actions */}
        <div className="hidden md:flex items-center gap-4">
          <Button asChild variant="ghost" className="text-neon hover:text-white">
            <Link to="/quickstart">Quickstart</Link>
          </Button>
          <Button
            variant="outline"
            className="items-center gap-2 border-neon text-neon hover:bg-neon hover:text-black"
            onClick={() => (isHomePage ? scrollToSection("download") : null)}
            as={isHomePage ? undefined : Link}
            to={isHomePage ? undefined : "/#download"}
          >
            <Download className="w-4 h-4" />
            <span>Download</span>
          </Button>

          <Button
            variant="ghost"
            className="text-white hover:text-neon"
            onClick={() => window.open("https://ellowdigital.netlify.app/", "_blank")}
          >
            EllowDigital
          </Button>
        </div>

        {/* Mobile Navigation */}
        <div className="md:hidden flex items-center">
          <Sheet open={mobileMenuOpen} onOpenChange={setMobileMenuOpen}>
            <SheetTrigger asChild>
              <Button variant="outline" size="icon" className="text-neon border-neon">
                <Menu className="h-5 w-5" />
              </Button>
            </SheetTrigger>
            <SheetContent
              side="right"
              className="bg-black/95 border-neon/20 text-white w-72 p-6 flex flex-col"
            >
              <SheetHeader className="text-left text-white space-y-1">
                <p className="text-xs uppercase tracking-widest text-muted-foreground">Navigate</p>
                <SheetTitle className="text-2xl font-semibold text-white">Menu</SheetTitle>
                <SheetDescription className="text-sm text-white/70">
                  Choose a section or jump straight to the quickstart guide.
                </SheetDescription>
              </SheetHeader>
              <div className="mt-8 flex flex-col gap-3">
                {sectionLinks.map((link) =>
                  isHomePage ? (
                    <button
                      key={link.id}
                      onClick={() => scrollToSection(link.id)}
                      className="text-left text-lg font-medium text-white/80 hover:text-white"
                    >
                      {link.label}
                    </button>
                  ) : (
                    <Link
                      key={link.id}
                      to={`/#${link.id}`}
                      onClick={() => setMobileMenuOpen(false)}
                      className="text-lg font-medium text-white/80 hover:text-white"
                    >
                      {link.label}
                    </Link>
                  )
                )}
                <Link
                  to="/quickstart"
                  onClick={() => setMobileMenuOpen(false)}
                  className="text-lg font-semibold text-neon"
                >
                  Quickstart Guide
                </Link>
              </div>
              <div className="mt-auto flex flex-col gap-3 pt-8">
                {isHomePage ? (
                  <Button
                    className="w-full items-center gap-2"
                    onClick={() => scrollToSection("download")}
                  >
                    <Download className="w-4 h-4" />
                    Download App
                  </Button>
                ) : (
                  <Button asChild className="w-full items-center gap-2">
                    <Link to="/#download" onClick={() => setMobileMenuOpen(false)}>
                      <Download className="w-4 h-4" />
                      Download App
                    </Link>
                  </Button>
                )}
                <Button
                  variant="ghost"
                  className="text-white hover:text-neon"
                  onClick={() => {
                    setMobileMenuOpen(false);
                    window.open("https://ellowdigital.netlify.app/", "_blank");
                  }}
                >
                  EllowDigital
                </Button>
              </div>
            </SheetContent>
          </Sheet>
        </div>
      </div>
    </header>
  );
};

export default Navbar;
