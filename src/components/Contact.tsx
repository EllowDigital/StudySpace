import { useState, useRef, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  User,
  Mail,
  MessageSquare,
  Target,
  Phone,
  Clock,
  MailCheck,
  Sparkles,
  Send,
} from "lucide-react";
import { useToast } from "@/hooks/use-toast";
import { cn } from "@/lib/utils";
import axios from "axios";
import { Link } from "react-router-dom";

const contactReasons = [
  {
    id: "feature",
    label: "Feature idea",
    subject: "Feature Suggestion",
    description: "Share a workflow boost or UI tweak you'd like to see next.",
  },
  {
    id: "support",
    label: "Need help",
    subject: "App Update Problem",
    description: "Report issues blocking your drills or dashboard.",
  },
  {
    id: "partnership",
    label: "Partner with us",
    subject: "Partnership Inquiry",
    description: "Collaborations, bundles, or content features.",
  },
];

const responseStats = [
  { label: "Avg. response", value: "< 6 hrs" },
  { label: "Uptime", value: "99.9%" },
  { label: "Community", value: "27K+" },
];

// Reusable input component with icon and error message
const InputField = ({
  icon,
  name,
  type,
  placeholder,
  value,
  onChange,
  error,
}: {
  icon: React.ReactNode;
  name: string;
  type: string;
  placeholder: string;
  value: string;
  onChange: React.ChangeEventHandler<HTMLInputElement>;
  error?: string;
}) => (
  <div className="space-y-1">
    <div className="relative">
      <div className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">{icon}</div>
      <Input
        name={name}
        type={type}
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className="pl-10 bg-dark border-white/10 focus-visible:ring-neon"
        required
      />
    </div>
    {error && <p className="text-sm text-red-500">{error}</p>}
  </div>
);

const Contact = () => {
  const [formData, setFormData] = useState({
    name: "",
    email: "",
    subject: "",
    message: "",
  });

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const sectionRef = useRef<HTMLElement>(null);
  const { toast } = useToast();

  // Fade-in animation on scroll
  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          entry.target.classList.add("animate-fade-in");
        }
      },
      { threshold: 0.1 }
    );

    if (sectionRef.current) observer.observe(sectionRef.current);
    return () => observer.disconnect();
  }, []);

  // Validate form fields
  const validateForm = () => {
    const newErrors: Record<string, string> = {};

    if (!formData.name.trim()) newErrors.name = "Name is required";
    if (!formData.email.trim()) {
      newErrors.email = "Email is required";
    } else if (!/^[\w-.]+@([\w-]+\.)+[\w-]{2,4}$/i.test(formData.email)) {
      newErrors.email = "Invalid email format";
    }

    if (!formData.subject) newErrors.subject = "Subject is required";
    if (!formData.message.trim()) newErrors.message = "Message is required";

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  // Handle input change
  const handleChange = (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>
  ) => {
    const { name, value } = e.target;
    setFormData((prev) => ({ ...prev, [name]: value }));
    setErrors((prev) => ({ ...prev, [name]: "" }));
  };

  // Submit form and send email
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!validateForm()) return;

    setIsSubmitting(true);

    const payload = {
      ...formData,
      _subject: "📩 New TypeBlitz - Submission",
      _template: "table",
      _autoresponse: `Hi ${formData.name},

Thanks for reaching out to us at TypeBlitz! 🎉

We’ve received your message and will get back to you as soon as possible.

- Subject: ${formData.subject}
- Message: ${formData.message}

Regards,  
The EllowDigital Team  
www.ellowdigitals.me`,
      _replyto: formData.email,
      _cc: "sarwanyadav6174@gmail.com",
      _captcha: "false",
    };

    try {
      await axios.post("https://formsubmit.co/ajax/ellowdigitals@gmail.com", payload);
      toast({ title: "Message Sent!", description: "We'll get back to you shortly." });
      setFormData({ name: "", email: "", subject: "", message: "" });
    } catch (error) {
      console.error("Form submission error:", error);
      toast({
        title: "Submission Failed",
        description: "Something went wrong. Please try again later.",
        variant: "destructive",
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  const messageCharCount = formData.message.length;

  return (
    <section
      id="contact"
      ref={sectionRef}
      className="section-padding bg-gradient-to-t from-black to-dark relative opacity-0"
    >
      <div className="container px-4 md:px-6">
        <div className="text-center mb-16 space-y-4">
          <div className="inline-flex items-center gap-2 rounded-full border border-white/10 px-4 py-1 text-xs uppercase tracking-[0.35em] text-muted-foreground">
            <Sparkles className="h-3.5 w-3.5" /> Help desk
          </div>
          <h2 className="section-title mb-2">Get in Touch</h2>
          <p className="section-subtitle max-w-3xl mx-auto">
            Share feedback, request a feature, or flag a bug. We respond faster than your average
            status LED.
          </p>
        </div>

        <div className="grid gap-10 lg:grid-cols-[1.05fr_0.65fr]">
          <div className="glass rounded-3xl border border-white/10 p-6 md:p-8">
            <div className="mb-6 space-y-3">
              <p className="text-sm uppercase tracking-[0.4em] text-muted-foreground">
                Pick a reason
              </p>
              <div className="grid gap-3 md:grid-cols-3">
                {contactReasons.map((reason) => {
                  const isActive = formData.subject === reason.subject;
                  return (
                    <button
                      key={reason.id}
                      type="button"
                      onClick={() =>
                        setFormData((prev) => ({ ...prev, subject: reason.subject.toLowerCase() }))
                      }
                      className={cn(
                        "rounded-2xl border px-4 py-3 text-left transition",
                        isActive
                          ? "border-neon/70 bg-neon/10"
                          : "border-white/5 bg-white/0 hover:border-white/20"
                      )}
                    >
                      <p className="text-sm font-semibold text-white">{reason.label}</p>
                      <p className="text-xs text-white/70">{reason.description}</p>
                    </button>
                  );
                })}
              </div>
            </div>

            <form onSubmit={handleSubmit} className="space-y-6">
              <div className="grid gap-6 md:grid-cols-2">
                <InputField
                  icon={<User className="w-5 h-5" />}
                  name="name"
                  type="text"
                  placeholder="Your Name"
                  value={formData.name}
                  onChange={handleChange}
                  error={errors.name}
                />

                <InputField
                  icon={<Mail className="w-5 h-5" />}
                  name="email"
                  type="email"
                  placeholder="Your Email"
                  value={formData.email}
                  onChange={handleChange}
                  error={errors.email}
                />
              </div>

              <div className="space-y-2">
                <div className="relative">
                  <div className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">
                    <Target className="w-5 h-5" />
                  </div>
                  <select
                    name="subject"
                    value={formData.subject}
                    onChange={handleChange}
                    className="pl-10 bg-dark border-white/10 focus-visible:ring-neon w-full rounded py-2"
                    required
                  >
                    <option value="">Please select a subject</option>
                    <option value="general feedback">General Feedback</option>
                    <option value="feature suggestion">Feature Suggestion</option>
                    <option value="app update problem">Issue After App Update</option>
                    <option value="app fails on start">Application Fails to Launch</option>
                    <option value="network error">Network Connectivity Issue</option>
                    <option value="typing stats not saved">Typing Stats Not Saving</option>
                    <option value="dashboard not working">Dashboard Not Functioning</option>
                    <option value="partnership inquiry">Partnership Inquiry</option>
                  </select>
                </div>
                {errors.subject && <p className="text-sm text-red-500">{errors.subject}</p>}
              </div>

              <div className="space-y-2">
                <div className="relative">
                  <div className="absolute left-3 top-3 text-muted-foreground">
                    <MessageSquare className="w-5 h-5" />
                  </div>
                  <Textarea
                    name="message"
                    value={formData.message}
                    onChange={handleChange}
                    placeholder="Share details, reproduction steps, or expected behavior"
                    className="pl-10 bg-dark border-white/10 min-h-[140px] focus-visible:ring-neon"
                    required
                  />
                  <div className="absolute right-4 bottom-3 text-xs text-muted-foreground">
                    {messageCharCount}/1000
                  </div>
                </div>
                {errors.message && <p className="text-sm text-red-500">{errors.message}</p>}
              </div>

              <Button
                type="submit"
                className="w-full bg-neon text-dark hover:bg-neon/90 btn-glow"
                disabled={isSubmitting}
              >
                {isSubmitting ? (
                  <span className="flex items-center justify-center gap-2">
                    <svg
                      className="h-4 w-4 animate-spin text-dark"
                      xmlns="http://www.w3.org/2000/svg"
                      fill="none"
                      viewBox="0 0 24 24"
                    >
                      <circle
                        className="opacity-25"
                        cx="12"
                        cy="12"
                        r="10"
                        stroke="currentColor"
                        strokeWidth="4"
                      ></circle>
                      <path
                        className="opacity-75"
                        fill="currentColor"
                        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
                      ></path>
                    </svg>
                    Sending...
                  </span>
                ) : (
                  <span className="flex items-center justify-center gap-2">
                    Send Message <Send className="h-4 w-4" />
                  </span>
                )}
              </Button>
            </form>
          </div>

          <aside className="space-y-6">
            <div className="rounded-3xl border border-white/5 bg-black/40 p-6">
              <p className="text-sm uppercase tracking-[0.4em] text-muted-foreground">Reach us</p>
              <div className="mt-4 space-y-4 text-sm text-white/80">
                <div className="flex items-center gap-3">
                  <MailCheck className="h-4 w-4 text-neon" />
                  <a href="mailto:ellowdigitals@gmail.com" className="hover:text-neon">
                    ellowdigitals@gmail.com
                  </a>
                </div>
                <div className="flex items-center gap-3">
                  <Phone className="h-4 w-4 text-neon" />
                  <span>+91 6205 621 623</span>
                </div>
                <div className="flex items-center gap-3">
                  <Clock className="h-4 w-4 text-neon" />
                  <span>Mon–Sat · 9:00–20:00 IST</span>
                </div>
              </div>

              <div className="mt-6 grid gap-3 sm:grid-cols-3">
                {responseStats.map((stat) => (
                  <div
                    key={stat.label}
                    className="rounded-2xl border border-white/10 bg-white/5 px-3 py-2 text-center"
                  >
                    <p className="text-lg font-semibold text-white">{stat.value}</p>
                    <p className="text-[11px] uppercase tracking-[0.25em] text-white/60">
                      {stat.label}
                    </p>
                  </div>
                ))}
              </div>

              <Button
                variant="outline"
                className="mt-6 w-full border-white/20 text-white hover:bg-white/5"
                asChild
              >
                <Link to="/quickstart">Browse Quickstart Guide</Link>
              </Button>
            </div>

            <div className="rounded-3xl border border-white/5 bg-white/5 p-6 text-sm text-white/80">
              <p className="text-sm uppercase tracking-[0.4em] text-muted-foreground">Status</p>
              <p className="mt-2 text-lg font-semibold text-white">Systems nominal</p>
              <p className="mt-2 text-white/70">
                Monitored by TypeBlitz Ops. Expect proactive updates via the in-app toast feed for
                any incidents.
              </p>
            </div>
          </aside>
        </div>
      </div>
    </section>
  );
};

export default Contact;
