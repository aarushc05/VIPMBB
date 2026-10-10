import React from "react";
import {
  ArrowDown,
  ArrowUpRight,
  BookOpen,
  ClipboardList,
  MessageSquare,
  ShieldCheck,
} from "lucide-react";

function openLibrary() {
  const target = document.getElementById("session-library");
  target?.focus({ preventScroll: true });
  target?.scrollIntoView({
    behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ? "instant"
      : "smooth",
    block: "start",
  });
}

// An original decorative court drawing, not a tracking plot or an official logo.
function Court() {
  return (
    <div className="hero-court" aria-hidden="true">
      <div className="court-caption">
        <span>MCCAMISH</span>
        <span>ATL / GA</span>
      </div>
      <svg viewBox="0 0 440 360" fill="none">
        <g className="court-lines">
          <rect x="30" y="30" width="380" height="300" rx="3" />
          <path d="M220 30v300" />
          <circle cx="220" cy="180" r="44" />
          <path d="M30 110h100v140H30M410 110H310v140h100" />
          <circle cx="130" cy="180" r="38" />
          <circle cx="310" cy="180" r="38" />
          <path d="M30 62h24c168 0 168 236 0 236H30M410 62h-24c-168 0-168 236 0 236h24" />
          <path d="M57 159v42M383 159v42" />
          <circle cx="67" cy="180" r="9" />
          <circle cx="373" cy="180" r="9" />
        </g>
        <g className="court-axis">
          <path d="M18 18h22M18 18v22M422 18h-22M422 18v22M18 342h22M18 342v-22M422 342h-22M422 342v-22" />
        </g>
      </svg>
      <div className="court-signature">
        <span>THE WORK BEHIND THE GAME</span>
        <span>01 / MBB</span>
      </div>
    </div>
  );
}

export function PracticeHero({ navigate, demo }) {
  return (
    <section
      className="program-hero"
      data-reveal
      aria-labelledby="practice-title"
    >
      <div className="hero-copy">
        <div className="eyebrow">
          <span className="gold-rule" /> GEORGIA TECH · MEN’S BASKETBALL
        </div>
        <h1 id="practice-title">
          The work behind
          <br />
          <em>the game.</em>
        </h1>
        <p>
          Turn every recorded session into a clearer conversation. Player
          workloads, practice context, and answers grounded in the data.
        </p>
        <div className="hero-actions">
          <button className="button primary" onClick={openLibrary}>
            Explore sessions <ArrowDown size={17} />
          </button>
          <button
            className="button secondary"
            onClick={() => navigate("assistant")}
          >
            Ask a question <ArrowUpRight size={17} />
          </button>
        </div>
        <div className="hero-assurance">
          <ShieldCheck size={15} />
          {demo
            ? "Synthetic demo · no real athlete data"
            : "Private by design. Built for the practice floor."}
        </div>
      </div>
      <Court />
    </section>
  );
}

export function WorkflowLinks({ navigate }) {
  return (
    <section
      className="workflow-grid"
      aria-label="Analysis workflow"
      data-reveal
    >
      {[
        {
          step: "01",
          icon: ClipboardList,
          title: "Review the session",
          detail: "Coverage first. Workload in context.",
          action: openLibrary,
        },
        {
          step: "02",
          icon: MessageSquare,
          title: "Follow the question",
          detail: "Ask naturally. Check the sources.",
          action: () => navigate("assistant"),
        },
        {
          step: "03",
          icon: BookOpen,
          title: "Keep the context",
          detail: "Definitions and notes, together.",
          action: () => navigate("knowledge"),
        },
      ].map(({ step, icon: Icon, title, detail, action }) => (
        <button key={step} className="workflow-card" onClick={action}>
          <span className="workflow-step">{step}</span>
          <Icon size={20} />
          <span>
            <strong>{title}</strong>
            <span>{detail}</span>
          </span>
          <ArrowUpRight size={17} />
        </button>
      ))}
    </section>
  );
}
