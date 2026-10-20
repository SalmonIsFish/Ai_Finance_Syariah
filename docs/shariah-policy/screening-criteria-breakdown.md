# Shariah Screening Criteria (SC Malaysia)

Source: [[Shariah-compliant-May 2026_final]]
Last verified: [May 2026]

## 1. Business Activity Benchmark

**Single 5% benchmark.** The contribution of all Shariah non-compliant businesses
or activities to the group's total income must be **less than 5%**. The SAC
removed the former 20% benchmark (SAC 288th meeting, 24 Feb 2025; 296th meeting,
13 Nov 2025), effective for listed companies with financial years ending on or
after 31 December 2025. Sources: sc.com.my, "Resolutions of the Shariah Advisory
Council of the SC" and "Shariah-compliant securities screening methodology"
(checked 2026-10-20).

How this repo applies it: the US screen (`sec_edgar_screen.py`) cannot measure a
percentage. It excludes by SEC industry code (SIC) all or nothing. The
percentage is read from the annual report by `filing_review.py`, and that reading
can only make a holding YELLOW (from 4.5%), never decide it.

The activities counted towards it:

- [ ] Conventional banking / interest-based lending
- [ ] Conventional insurance
- [ ] Gambling
- [ ] Alcohol / liquor-related activities
- [ ] Pork and pork-related products
- [ ] Conventional leasing (non-Islamic ijarah structures)
- [ ] non-halal food and beverages (F&B) including F&B without halal certification
- [ ] tobacco, cigarette, electronic cigarettes and their related activities and products
- [ ] dividends from Shariah non-compliant investments;
- [ ] Shariah non-compliant entertainment
- [ ] share trading
- [ ] stockbroking business
- [ ] cinema
- [ ] rental received from Shariah non-compliant activities
- [ ] other activities deemed non-compliant according to Shariah principles as determined by the SAC

## 2. Financial Ratio Benchmarks (exact numbers)

For the financial ratio benchmarks, the SAC takes into account the following:

### Cash over total assets:
Cash only includes cash placed in conventional accounts and instruments,
whereas cash placed in Islamic accounts and instruments is excluded
from the calculation.

### Debt over total assets 
Debt only includes interest-bearing debt whereas Islamic financing or
sukuk is excluded from the calculation.
Each ratio, which is intended to measure riba and riba-based elements within a
company’s statements of financial position, must be less than 33%.
In addition to the above two-tier quantitative criteria, the SAC also takes into
account the qualitative aspect which involves public perception or image of the
company’s activities from the perspective of Islamic teaching.
Shariah-compliant securities include ordinary shares and warrants (issued
by the companies themselves). This means that warrants are classified as
Shariah-compliant securities provided the underlying shares are also Shariah-compliant. On the other hand, loan stocks and bonds are Shariah noncompliant securities unless they are structured based on the SAC’s approved
Shariah rulings, concepts and principles.

### Special Purpose Acquisition Companies

In classifying securities of special purpose acquisition companies
(SPACs), the
SAC considers the following criteria:
- (i) The proposed business activity should be Shariah-compliant

- (ii) The entire proceeds raised from the initial public offering should be placed in Islamic accounts

- (iii) In the event that the proceeds are invested, the entire  investment should be Shariah-compliant.

### Timing for the disposal of Shariah non-compliant securities
As a general guidance to investors, the SAC would like to advise investors on the timing for the disposal of securities which have been classified as Shariah non-compliant. 

- (i) ‘Shariah-compliant securities’ which are subsequently re-classified as ‘Shariah non-compliant’ These refer to securities which were earlier classified as Shariahcompliant but due to certain factors such as changes in the companies’ business operations and financial positions, are subsequently reclassified as Shariah non-compliant. In this regard, if on the effective date, the respective market price of Shariah non-compliant securities exceeds or is equal to the investment cost, investors who hold such securities must dispose of them. Any dividends received up to the effective date and capital gains arising from the disposal of Shariah non-compliant securities on the effective date can be kept by the investors. However, any dividends received and excess capital gain from the disposal of Shariah non-compliant securities after the effective date should be channelled to baitulmal and/orcharitable bodies.

On the other hand, investors are allowed to hold their investment in the Shariah non-compliant securities if the market price of the said
securities is below the investment cost. It is also permissible for the investors to keep the dividends received during the holding period until such time when the total amount of dividends received and the market value of the Shariah non-compliant securities held equal the investment cost. At this stage, they are advised to dispose of their holding.In addition, during the holding period, investors are allowed to subscribe to:

- (a) any issue of new securities by a company whose Shariah noncompliant securities are held by the investors, for example rights
issues, bonus issues, special issues and warrants (excluding securities whose nature is Shariah non-compliant e.g. loan stocks)and

- (b) Shariah-compliant securities of other companies offered by the
company whose Shariah non-compliant securities are held by the
investors, 

on condition that they expedite the disposal of the Shariah non-compliant securities. 

- (ii) Shariah non-compliant securities
The SAC advises investors who invest based on Shariah principles to
dispose of any Shariah non-compliant securities which they presently
hold, within a month of knowing the status of the securities. Any gain
made in the form of capital gain or dividend received before or after
the disposal of the securities must be channelled to baitulmal and/or
charitable bodies. The investor has a right to retain only the investment cost.

## 3. Sector Compliance 

| Main / ACE / LEAP Markets by sector | Number of Shariahcompliant securities | Total securities* | Percentage of Shariah-compliant securities (%) |
| ----------------------------------- | ------------------------------------- | ----------------- | ---------------------------------------------- |
| Industrial products and services    | 271                                   | 320               | 85                                             |
| Consumer products and services      | 184                                   | 240               | 77                                             |
| Technology                          | 98                                    | 125               | 78                                             |
| Property                            | 86                                    | 100               | 86                                             |
| Construction                        | 75                                    | 79                | 95                                             |
| Energy                              | 37                                    | 43                | 86                                             |
| Plantation                          | 35                                    | 41                | 85                                             |
| Transportation and logistics        | 31                                    | 36                | 86                                             |
| Healthcare                          | 27                                    | 30                | 90                                             |
| Telecommunications and media        | 24                                    | 33                | 73                                             |
| Utilities                           | 11                                    | 13                | 85                                             |
| Financial services                  | 7                                     | 38                | 18                                             |
| Closed-end fund                     | Nil                                   | 1                 | Nil                                            |
| Total                               | 886                                   | 1,099             | 81                                             |


## 4. Review Cycle
- Frequency: Twice yearly — last Friday of May and last Friday of November
- Based on: Company's latest audited annual financial statements
- New listings: Newly listed companies are also assessed as part of
  the review — check the document's IPO/new-listing section for the
  exact timing rule, since I haven't independently verified this detail
- Next expected update: Last Friday of November 2026

## Notes for the Shariah Advisor Agent
- The screening methodology above is stable and safe to store statically.
- The actual list of compliant tickers is NOT stored statically here —
  it must be fetched live from sc.com.my at decision time, since it
  changes every May/November. A static list will silently go stale.
- Until live-fetch is built (Claude Code phase), maintain this manually:
  check sc.com.my last Friday of May and November, re-download and
  re-verify before trusting any compliance decision.