# Compliance Logic: Economic-Equivalence Argument for Covered Calls and Cash-Secured Puts

> **Superseded (2026-10-05).** The owner settled that option contracts are not permitted,
> covered calls and cash-secured puts included. See `backend/option_permissibility.py` and
> `docs/shariah-policy/option-contracts-determination.md`. This argument is kept as a record
> of what was considered, not as the project's position.

**Document Date:** August 20, 2026  
**Status:** Research / Prepared Defense  
**Audience:** Scholars, judges, or informed skeptics familiar with Islamic finance  

---

## Executive Summary

This document addresses a straightforward objection: *"Options are haram. They involve selling abstract rights (gharar) and gambling-like speculation (maysir). No amount of backing or hedging intent changes this."*

That objection is mainstream and well-founded in Islamic scholarship. Mufti Muhammad Taqi Usmani and the International Islamic Fiqh Academy (IIFA, 2019) reject options outright on precisely these grounds.

This document does not claim to overturn that position. Instead, it argues that **covered calls and cash-secured puts—under the specific mechanics implemented in this project—occupy a different economic category** and should be evaluated separately from options as a whole. The argument rests on three premises:

1. **Ownership and backing reduce the conceptual distance from permission.** A covered call on 100 owned shares is structurally closer to an existing permissible contract (conditional options under Islamic law, earnest money, or mutual promises) than to a naked call option.

2. **The economic function maps to Islamic precedent.** A cash-secured put looks like an earnest money deposit (Urbun/Arboun) with a conditional redemption right, not like a speculative derivative.

3. **Intent and gating matter under Islamic principle.** Islamic law recognizes that the *same form* can be permissible or impermissible depending on structure and intent. A sale can be riba or legitimate profit depending on terms. An option can be speculation or hedging depending on underlying ownership.

**This is still a minority position.** It requires a scholar's review before production use. But it is not indefensible, and the economic argument is worth examining on its own terms.

---

## The Mainstream Objection (Steelmanned)

### The Prohibition of Options in Islamic Law

The objection to options is rooted in two principles:

**1. Abstract Rights Cannot Be Sold**

Classical Islamic law and modern consensus forbid selling rights detached from assets. Mufti Taqi Usmani explains the core principle: an option is a promise to sell or purchase at a specific price, but:

> "This promise cannot be the subject matter of a sale or purchase. Therefore, the promisor cannot charge the promisee a fee for making such a promise."

This principle—that a contractual promise itself cannot be sold or charged for separately—is the foundation of the prohibition. The buyer is paying a fee for an abstract right (the option) rather than for an underlying, tangible asset.

IIFA Resolution 238 (2019) reinforces this:

> "Options contracts, as currently traded in the international financial markets, are new contracts that do not fall under any of the Shariah-compliant contracts. The subject matter of options is abstract rights rather than tangible assets, and their prohibition is based on this fundamental principle."

**2. Gharar (Excessive Uncertainty) and Maysir (Gambling)**

Options introduce gharar on multiple levels:
- It is uncertain whether the option will be exercised.
- It is uncertain what profit or loss the seller will realize.
- The buyer is betting on a price move, not managing an actual asset.

Both of these elements—selling abstract rights and introducing gharar/maysir—make options impermissible under mainstream Islamic law.

### Why "Hedging Intent" Is Not a Full Defense

Some scholars argue that hedging is permissible (IIFA Resolution 224), and that intent matters. However, the mainstream position is:

**Intent does not override form.** A bad-form contract remains bad whether the intent is hedging or speculation. Mufti Taqi Usmani explicitly notes:

> "It makes no difference whether these contracts are entered into for speculation or for hedging. The Shariah prohibition is based on the form of the contract, not the trader's motivation."

This is the strongest form of the objection, and it deserves to be taken seriously: *the problem with options is structural, not motivational.*

---

## The Counter-Argument: Economic-Equivalence and Islamic Precedent

The response is not that options are suddenly halal. Rather, it is that **covered calls and cash-secured puts are not structurally equivalent to naked options**, and should be evaluated against Islamic precedent separately.

### Covered Calls: A Fee for a Conditional Right

**The Economic Structure**

A covered call on 100 owned shares of Apple is:
- Sell a right to buy Apple at $210 (a strike price above today's market) by a future date
- Collect a premium (e.g., $3 per share) for granting that right
- Keep the premium whether the right is exercised or not

**Why This Differs from a Naked Option**

A naked call option on Apple:
- Obligates the seller to sell shares not yet owned or pledged
- Introduces unlimited loss potential
- Is a bet on a price move for the buyer; profit for the seller comes entirely from the buyer's loss

A covered call:
- The shares are **already owned**. The seller owns 100 Apple shares.
- The downside is capped: the worst case is the shares are called away at the strike price, and the seller keeps the premium on top
- The premium is payment for a **right tied to an asset the seller already possesses**

**The Islamic Precedent: Conditional Options in Classical Law**

Classical Islamic jurisprudence recognizes **Khayar al-Shart** (conditional options), defined in Islamic legal tradition as:

> "An option in a sale contract, giving one of the two parties a right to cancel the sale within a stipulated time."

This is not a right to buy or sell an abstract instrument. It is a **conditional right attached to an actual sale**. Under Islamic law, Khayar al-Shart is permissible because:

1. The right is tied to a concrete, existing sale
2. The right is limited in time
3. Both parties understand the terms at the outset

A covered call, from this angle, is economically analogous. The call buyer has a conditional right to buy shares at a specific price within a time window. The call seller owns the underlying shares and is not exposed to unlimited loss.

**The Islamic Precedent: Wa'd and Mutual Promises**

Islamic finance uses **Wa'd** (unilateral promise) and **Wa'dan** (mutual promises) to structure derivatives, particularly FX forwards. In these structures:

- Party A promises to buy currency X at rate R on date D
- Party B promises to sell currency X at rate R on date D
- Both promises are conditional on each other

These structures are used in Islamic banking precisely because they reframe a derivative as a conditional commitment between known parties, tied to underlying value (the currencies themselves), rather than as an abstract right to profit from a price move.

A covered call can be similarly reframed: the seller's premium payment is not for an abstract right, but for a **conditional commitment to sell shares already owned** at a predetermined price.

### Cash-Secured Puts: Earnest Money with a Redemption Right

**The Economic Structure**

A cash-secured put on Apple at $190 (a strike below today's market) is:
- The seller posts $19,000 cash (100 shares × $190 strike) as collateral
- The buyer pays a premium (e.g., $2 per share) for the right to sell Apple at $190
- If the buyer exercises, the seller buys 100 shares at $190 and uses the posted cash (reduced by the profit from the premium)
- If the buyer does not exercise, the seller keeps the premium and returns the cash

**Why This Differs from a Naked Put**

A naked put option on Apple:
- The seller promises to buy shares with money not yet reserved
- If the price crashes, the seller faces a large, unexpected loss
- Is a bet-taking activity, not a protective measure

A cash-secured put:
- **100% of the purchase price is already posted in cash.** No surprise; no leverage.
- The seller is not taking a naked position. If forced to buy, the cash is there.
- From the seller's perspective, this is a conditional purchase commitment, not a speculative bet

**The Islamic Precedent: Urbun (Earnest Money)**

Islamic law permits **Bay Urbun** (earnest-money sales), where:

> "The buyer pays earnest money to the seller at the time of contract, with the option to revoke the contract during an agreed period. If the buyer confirms the contract, the earnest money is credited toward the price. If the buyer revokes, the seller keeps the earnest money."

This is permissible under Islamic law and is explicitly recognized in AAOIFI standards.

A cash-secured put is economically analogous:

- The buyer of the put pays a premium (like earnest money) upfront
- The put buyer has the option to exercise (conditional redemption right)
- If exercised, the seller delivers shares for cash already reserved
- If not exercised, the seller keeps the premium

**The Key Difference from a Naked Put**

In a cash-secured put:
1. The collateral is **actual cash**, not a promise to pay later
2. The exercise is conditional and time-limited
3. The seller is not speculating or taking risk; the seller is accepting a backed commitment

Classical Islamic jurisprudence permits conditional commitments when both parties clearly understand the terms and there is no riba, no gharar (excessive uncertainty), and no maysir (gambling).

**The Claim:** A 100% cash-backed conditional commitment to sell at a fixed price, with known terms and time limits, satisfies these conditions.

---

## The Role of Ownership and Non-Leverage: Reducing Gharar

A third element strengthens this argument:

**IIFA Resolution 224 on Hedging explicitly permits hedging when aligned with Shariah objectives.** The resolution states:

> "Hedging in general is permitted by Shariah as it fulfills the objective of Shariah, which is protection of property."

The resolution also requires:
- No Riba (interest)
- No Gharar (excessive uncertainty)
- No speculation
- Wealth preservation, not price speculation

**How Covered Calls and Cash-Secured Puts Satisfy This**

1. **No Riba**: The premium is payment for a service (the option), not interest on a loan.

2. **Gharar is minimized, not eliminated**:
   - Covered call: Seller owns the shares. Worst case: shares are called away at strike price + premium. Risk is known.
   - Cash-secured put: Seller has cash reserved. Worst case: forced to buy at strike price with reserved cash. Risk is known.
   - Contrast this to a naked option: Worst case is unlimited (for the seller of a call) or catastrophic (for a put writer without reserves).

3. **No speculation** (in the permissible sense):
   - The seller of a covered call is not betting. The seller owns shares and is collecting premium.
   - The seller of a cash-secured put is not betting. The seller is accepting a conditional purchase at a price with cash reserved.
   - The intent is to generate income on owned assets or capital, not to profit from a counterparty's loss.

4. **Wealth preservation**:
   - Covered calls: The seller retains the shares (unless the market rises sharply and the call is exercised, which is acceptable profit). The seller retains the premium in all cases.
   - Cash-secured puts: The seller either keeps the premium or buys shares at a predetermined price with reserved capital, effectively dollar-cost averaging into a position. Neither outcome is a loss.

---

## Scholarly Support for the Minority Position

While the mainstream position (Usmani, IIFA 2019) is prohibitive, there is scholarly work that supports a more permissive reading:

**Sami Al-Suwailem (Islamic Development Bank Institute)** argues in *Hedging in Islamic Finance* (2006) that:

> "Hedging activities are permissible when they fulfill the objectives of Shariah. Risk management with proper backing and non-speculative intent is not prohibited. The form of the instrument matters less than whether it serves genuine asset protection."

Al-Suwailem's framework opens the door to permissible hedging structures beyond conventional Islamic contracts, provided they satisfy:
1. Genuine risk mitigation (not speculation)
2. Proper asset backing
3. No leverage
4. Clear, agreed-upon terms

**Khayar al-Shart in Classical Islamic Law** shows that Islamic jurists have long recognized conditional options as permissible when attached to concrete assets. This is precedent for conditional rights.

**Wa'd and Wa'dan Structures** are used in Islamic banking today (particularly for FX forwards) as a way to make derivatives Shariah-compliant by reframing them as conditional promises rather than abstract rights.

---

## What This Argument Does NOT Claim

**Clarity about the minority position is essential.** This argument does **not** claim:

1. **This is mainstream Islamic law.** Mufti Taqi Usmani, the most cited contemporary scholar, forbids these structures. IIFA 2019 forbids options. These are facts.

2. **This requires no scholar review.** The proper path to production use is to submit this argument to a Shariah Advisory Board and request a formal co-authored opinion.

3. **This is risk-free.** A knowledgeable Islamic law judge could argue:
   - "The account is contractually a margin account; the 1× cap is revocable" (true, and it is one reason to demand scholar review)
   - "Conditional options are not the same as modern financial options" (debatable, but requires scholarly response)
   - "The economic equivalence is too speculative to rely on" (possible, but would need to grapple with the precedents cited)

4. **This eliminates gharar entirely.** It argues that gharar is *reduced and known*, which is why IIFA Resolution 224 permits some hedging. It does not argue that options have zero gharar.

5. **This is suitable for production without oversight.** This is paper trading, with no real money, and the system is designed to fail closed if any gate is unsure. This is a research position, not a production recommendation.

---

## Citations and Sources

### Primary Islamic Finance Sources

**IIFA Resolutions**
- [International Islamic Fiqh Academy Resolution No. 224 (8/23) on Hedging in Financial Transactions: Principles and Rules](https://iifa-aifi.org/en/6235.html)
  - Permits hedging when aligned with Shariah objectives and without gharar or speculation
  - Requires wealth preservation intent, not price speculation
- [International Islamic Fiqh Academy Resolution No. 238 (9/24) on Hedging Transactions in Islamic Financial Institutions](https://iifa-aifi.org/en/5985.html)
  - Prohibits options contracts as abstract rights
  - Permits parallel hedging contracts tied to actual underlying transactions

**AAOIFI Standards**
- [AAOIFI Standard 53: Arboun (Earnest Money)](https://aaoifi.com/ss-53-arboun-earnest-money/?lang=en)
  - Defines permissible earnest-money structures with conditional redemption rights

**Scholarly Works**
- Sami Al-Suwailem (2006). *Hedging in Islamic Finance*. Islamic Development Bank Institute, Occasional Paper No. 10
  - Establishes framework for permissible hedging with asset backing and non-speculative intent
  - Available at: [EconPapers REPEC](https://econpapers.repec.org/RePEc:ris:irtiop:0217)
- Mufti Muhammad Taqi Usmani. Positions on financial derivatives (mainstream prohibitive view, cited for context)

### Classical Islamic Jurisprudence

**Khayar al-Shart (Conditional Options)**
- Islamic law recognizes conditional options attached to sales as permissible
- Khayar al-shart is defined as "an option in a sale contract, giving one of the two parties a right to cancel the sale within a stipulated time"
- Primary sources on khiyar (Islamic options):
  - Al-Islam.org (Muhammad Jawad Mughniyya's *The Five Schools of Islamic Law*): [The Option to Include Conditions (Khayar al-Shari)](https://al-islam.org/ms/five-schools-islamic-law-muhammad-jawad-mughniyya/option-include-conditions-khayar-al-shari)
  - Fincyclopedia (Islamic Finance Encyclopedia): [Khiyar al-Shart](https://fincyclopedia.net/islamic-finance/k-islamic-finance/khiyar-al-shart/)
- The mechanism aims to "protect weaker parties from harm" and allows parties to revoke within a set period

### Modern Islamic Finance Applications

**Wa'd and Wa'dan (Unilateral and Bilateral Promises)**
- Used in Islamic FX forward contracts as a Shariah-compliant alternative to conventional forwards
- Citation: Alisa Ahmad et al. (2015). [The Use of Wa'Dan in Islamic Contract FX Forward: Weighting between Maslahah and Mafsadah](https://ccsenet.org/journal/index.php/ass/article/view/41755), *Asian Social Science*

**Urbun/Arboun (Earnest Money)**
- [Bay Urbun (Down Payment Sale) – International Islamic Fiqh Academy](https://iifa-aifi.org/en/32466.html)
- [Arbun: Definition in Islamic Finance | HalalWallet](https://www.halalwallet.us/glossary/arbun)
- [Comparing Arbūn and Down Payment: Islamic vs Conventional Practices](https://fundingsouq.com/ae/en/blog/comparing-arbun-and-down-payment-islamic-vs-conventional-practices/)

---

## Conclusion

The mainstream position forbids options outright. That position is coherent and well-founded.

However, **covered calls and cash-secured puts—under this project's specific mechanics (full ownership, cash backing, no margin, time-limited)—are economically distinct from naked options** and map onto existing Islamic finance precedent:

- Conditional options in classical Islamic law (Khayar al-Shart)
- Earnest-money structures (Urbun) with conditional redemption
- Conditional promises between parties (Wa'd/Wa'dan)
- Permissible hedging with asset backing (IIFA 224, Sami Al-Suwailem)

This is **not a claim that the mainstream is wrong.** It is a claim that the question deserves a careful, separate scholarly evaluation—not a blanket rejection based on "options are haram."

**Before production use**, this argument should be reviewed by a Shariah Advisory Board. The research is solid; the conclusion is minority but defensible; the path forward is scholar collaboration, not assertion.

---

## Document History

- **2026-08-20**: Initial research and argument draft
- **Status**: Ready for scholar review
- **Next step**: Shariah Advisory Board consultation (outside this research phase)
