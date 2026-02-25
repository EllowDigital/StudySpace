import { useEffect } from "react";
import Navbar from "@/components/Navbar";
import Footer from "@/components/Footer";
import ScrollToTop from "@/components/ScrollToTop";

const Privacy = () => {
  useEffect(() => {
    window.scrollTo(0, 0);
  }, []);

  return (
    <>
      <Navbar />
      <main className="container px-4 md:px-6 py-12 max-w-4xl mx-auto">
        <h1 className="text-3xl md:text-4xl font-bold mb-6 text-gradient">Privacy Policy</h1>

        <div className="space-y-6 text-muted-foreground">
          <section>
            <h2 className="text-xl font-semibold mb-3 text-foreground">1. Introduction</h2>
            <p>
              TypeBlitz is a desktop typing speed and accuracy assessment application developed by
              EllowDigital Enterprise. This Privacy Policy explains how TypeBlitz handles user
              information. The core principle of TypeBlitz is privacy-first: the app is designed to
              operate entirely on the user's device.
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold mb-3 text-foreground">2. Data Collection</h2>
            <p>
              TypeBlitz does not collect, transmit, store, or share any personal data. All typing
              statistics, scores, and history are stored locally on your device only and are never
              transmitted to external servers.
            </p>
            <p>
              The application does not require user accounts, logins, or any identity information to
              function.
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold mb-3 text-foreground">3. Offline Usage</h2>
            <p>
              TypeBlitz is fully functional offline. No data is sent automatically in the
              background. Any telemetry or analytics services are not used by this application.
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold mb-3 text-foreground">4. External Links</h2>
            <p>
              The app may open external web links only when explicitly initiated by you (for
              example, when visiting the official website). TypeBlitz does not send data to those
              sites automatically.
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold mb-3 text-foreground">5. No Tracking</h2>
            <p>
              We do not use analytics services, advertising networks, trackers, or third-party data
              collection tools. There is no hidden telemetry; the app operates locally and respects
              your privacy by design.
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold mb-3 text-foreground">
              6. Updates to This Policy
            </h2>
            <p>
              We may update this Privacy Policy occasionally. Any changes will be posted on this
              page and the "Last updated" date below will be revised.
            </p>
          </section>

          <section>
            <h2 className="text-xl font-semibold mb-3 text-foreground">7. Contact Us</h2>
            <p>
              If you have any questions about this Privacy Policy, please contact us at
              ellowdigitalindia@gmail.com.
            </p>
          </section>

          <div className="pt-4 border-t border-white/10 text-sm">Last updated: November 2025</div>
        </div>
      </main>
      <Footer />
      <ScrollToTop />
    </>
  );
};

export default Privacy;
