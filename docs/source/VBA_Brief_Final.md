# For Voxaris Arrivia VBA Brief Final

> Source: `/Users/voxaris/Downloads/For Voxaris Arrivia_VBA_Brief_Final.docx`

ARRIVIA

Virtual Booking Agent (VBA) — Executive Program Brief 5.1.26 SS for Voxaris

Internal Strategic Review | Arrivia Strategic Partnerships

Program Overview

The Arrivia Virtual Booking Agent (VBA) is a white-labeled, AI-powered engagement tool designed to help timeshare resort partners drive qualified tour bookings while guests are onsite or at offsite OPC third party locations — with less payroll and greater reach.  Arrivia deploys the technology, manages the process of booking, and captures tour qualifying information to pass to the partner as a service to add value to the relationship.

The Opportunity

Today, timeshare resorts rely on on-property OPC (On and Off-Property) agents stationed around the resort and at third-party locations to verbally offer guests incentives and qualify them for a sales tour. This approach is costly in payroll and limited in hours. Arrivia's VBA augments this with an always-on, branded experience and can work in conjunction with staffed inbound call centers or stand alone. Because this is not proven, we need to test this. Important to note: Timeshare is not a sought after product, it is sold. Hooking someone for a timeshare tour is not easy. Therefore, this is augmenting current processes in an effort to try to scale it virtually.

How It Works — Step by Step

OPC Qualification Standards

The following criteria are used by the AI agent (and live agents) to pre-qualify prospects before booking a tour. These standards are based on industry-standard OPC qualification protocol and determine whether a prospect is likely to purchase.

Core Qualification Criteria

AI Agent Qualification Flow

The AI agent follows this structured sequence during the qualification call:

Introduction & Hook: Engage prospect with the resort credit or premium offer.

Rapport Building: Establish friendly conversation and trust before qualification begins.

Soft Qualification: Ask about travel habits, occupation, and origin to gauge fit.

Hard Qualification (Subtle): Confirm income range, relationship status, decision-maker presence, tour history, and residency.

Confirmation & Close: Secure commitment to attend. Reinforce the incentive and set expectations (90–120 min presentation).

Behavioral Qualification Signals

In addition to hard criteria, agents should assess:

Buyer Indicators: Signs of disposable income, travel behavior, lifestyle cues

Decision Dynamics: Who makes financial decisions, alignment between partners

Travel Habits: Frequency of travel, interest in resorts, cruises, or vacation ownership

Personality Fit: Openness to new experiences vs. resistance or skepticism

Qualification Outcome

If all core criteria are met → Prospect is booked as a Qualified Tour.

If criteria are not met → Prospect is filtered out or redirected. No tour is booked and no fee is owed.

Compliance & Legal Addendum

This section is for internal planning purposes only and needs to be reviewed by legal counsel before deployment. It does not constitute legal advice.

The Core Risk: When Data Is Captured, Not Who Books the Tour

The practical risk in the VBA program is not that a minor would show up to a timeshare tour — the agent qualification process would catch that. The real legal exposure sits in the moment between the QR scan and the agent call, when phone number and device data have already been captured. Three design decisions eliminate this exposure entirely.

Three Compliance Fixes Built Into the Program Flow

Age Gate on the Landing Page. The very first screen after scanning asks the user to confirm they are 18 or older before anything is collected. The FTC allows “mixed audience” platforms to implement age-screening mechanisms before applying data collection. A simple “I confirm I am 18+” checkbox before the ad plays costs nothing and creates a meaningful legal shield.

Collect Nothing Until Age Is Confirmed. The phone number capture and outbound dial trigger should fire only after the age gate is passed — not at the moment of scan. The sequence is: QR scan → landing page only → age + consent gate → data capture + call trigger.

Add a Privacy Notice / Consent Line. The landing page includes: “By continuing, I agree to be contacted about this offer via automated call or text.” This covers TCPA consent for the Model A outbound dial and marketing consent for adults.

Legal Risk Summary

COPPA — Children Under 13

The Children’s Online Privacy Protection Act (COPPA) is enforced by the FTC and covers any online collection of personal data from children under 13 — including device IDs, phone numbers, IP addresses, and geolocation. The FTC finalized major COPPA updates in early 2025, raising fines to up to $51,744 per child, per violation. An age confirmation checkbox before any data flows is a well-established, FTC-recognized protective measure.

State Laws — Teens Ages 13–17

A fast-growing body of state legislation is extending data privacy protections to teenagers 13–17. Since timeshare resorts operate nationally, Arrivia must account for this landscape. Key examples:

Why the 18+ gate covers all of this: By requiring confirmed age 18+ before any data collection, the VBA program sidesteps every teen privacy law entirely. No data, no exposure, no state law issue.

TCPA — The Autodialer Rule (Critical for Model A)

The Telephone Consumer Protection Act (TCPA) governs automated or pre-recorded calls and texts to cell phones. Model A sits squarely in TCPA territory. TCPA fines range from $500 to $1,500 per call, and class action lawsuits under TCPA are among the most costly in consumer law. The gate consent checkbox constitutes prior express written consent under TCPA — provided the language specifically mentions automated calls.

Suggested consent language (must be reviewed by counsel before launch): “By checking this box, I consent to receive automated calls and texts from Arrivia at the number associated with this device regarding this resort credit offer.”

Model B (inbound tap-to-call) carries far lower TCPA risk because the guest initiates the call. This is a meaningful advantage in the A/B test design.

Recommended Legal Actions Before Launch

Priority: Engage privacy counsel to draft and approve the exact age gate consent language. The wording of both checkboxes is legally significant and cannot be generic boilerplate.

Priority: Review TCPA compliance for Model A specifically. Confirm that QR-scan-to-consent-to-autodial constitutes valid prior express written consent under current FCC guidance.

Confirm AI disclosure requirements by state. Several states require that an AI caller identify itself before proceeding. Applies to after-hours AI agent in both Model A and B.

Review white-label vendor agreement. Confirm non-disclosure to the timeshare partner is permissible and that liability is clearly allocated between Arrivia and the AI vendor.

Arrivia Virtual Booking Agent — Internal Working Document — Not for External Distribution
