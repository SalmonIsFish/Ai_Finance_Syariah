# Expert validation — methodology scaffold

Status: **scaffold. Citations and panel size are the author's to verify and
justify — this file deliberately does not decide them.**

Prepared 2026-09-22 following a Shariah-expert lecturer's guidance.

## Why this file is a scaffold and not an answer

The lecturer's guidance was specific and it opens a path that does not require
funding a committee:

- BNM's Shariah Governance Framework (2019, p.13) requires a licensed Islamic
  Financial Institution to have **at least five Shariah Committee members**.
- That is the **institutional** requirement, for an IFI.
- **For a non-commercialised academic project, the appropriate number of experts
  may be determined using established expert-validation methodology**, justified
  by reference to methodological literature, rather than by applying the IFI rule
  directly.

So the task is not "hire five scholars." It is "choose a validation method,
justify the panel size against the literature for that method, and document it."
That is a methods chapter, and methods chapters are the author's work.

This file therefore lays out the decisions to be made and where each must be
grounded. It does **not** assert a panel size or cite specific sources, because
those citations have to be read and verified by the person who will defend them.

## Decision 1 — Which validation method

The two families most commonly used for validating an instrument or framework
against expert judgement:

| Approach | Shape | Suits |
|---|---|---|
| **Content validity** (e.g. a content validity index) | Experts rate each item for relevance/clarity; ratings aggregate into an index with an acceptance threshold that varies with panel size | A fixed set of discrete items to validate — which is what the reviewer packet is |
| **Delphi** | Iterative rounds with controlled feedback until consensus criteria are met | Questions where expert opinion is expected to diverge and converge — plausibly the options and margin questions |

**To verify before committing:** the acceptance threshold in content-validity
approaches is a function of panel size, and the commonly cited tables originate
in specific papers. Read the primary source rather than a secondary summary; the
repo's own notes already flag elsewhere that secondary summaries were used where
primary standards were not obtained, and that is exactly the weakness to avoid
here.

## Decision 2 — Panel size, and its justification

Not asserted here. What the justification must contain:

1. The method chosen (Decision 1).
2. The panel size that method's literature supports, cited to a primary source.
3. Why that size is appropriate for a non-commercial academic artifact, with
   explicit acknowledgement that the BNM figure of five applies to licensed IFIs
   and is not being claimed as satisfied.
4. Expert inclusion criteria — qualification, domain, and why Islamic capital
   markets specifically rather than Islamic finance generally.

**Do not** justify a smaller panel by arguing the project is small. Justify it by
the method's own standards for instrument validation.

## Decision 3 — What the panel validates

Not the codebase. `reviewer-packet.md` lists the four questions where this system
makes its own determination and therefore needs a ruling:

1. The margin account — blocking, and may disqualify the broker entirely.
2. The permitted option structures — loosened for a deadline, never re-vetted.
3. Purification — three fiqh questions the code deliberately leaves open.
4. The US ratio screen — an approximation, with the qualitative limb unimplemented.

Malaysian security eligibility is explicitly **not** among them: the SC's SAC
classifies securities and this system applies that classification. The converse
matters just as much, and the lecturer stated it directly — being on the SC list
does not certify an automated trading system.

## Decision 4 — What may be claimed, and when

Until the panel reports, nothing in this project may describe itself as
Shariah-compliant, certified or validated. The defensible description is narrower
and still substantive:

> Applies the Securities Commission Malaysia SAC list of Shariah-compliant
> securities, and proves that application with an auditable evidence trail.

Two further constraints, both from the reviewer:

- **"Shariah-aware" is not an established term.** Do not coin it.
- Never write that algorithmic trading is compliant *because* it is systematic.
  The defensible form: *"the use of a systematic or algorithmic trading strategy
  does not, by itself, constitute maysir; Shariah compliance depends on the
  underlying securities, transaction structure, trading mechanism and applicable
  Shariah principles."*

## A note on what is genuinely novel here

The lecturer identified the research gap himself: **a Shariah governance framework
for algorithmic trading in Islamic capital markets.** He also confirmed he is not
aware of an AAOIFI, BNM or IFSB standard written for the Shariah governance of
algorithmic equity execution.

That means the contribution is not the screening — the SC already does that. It
is the governance arrangement: deterministic gates outside the decision-making
component, a human-authorised mandate (`docs/TRADING_MANDATE.md`, generated from
enforced configuration so it cannot drift), and an append-only evidence trail
recording refusals as well as approvals.

Whether that arrangement is an established pattern under any Shariah governance
framework, or genuinely novel, is itself a question for the literature — and if
novel, it needs support rather than assertion.
