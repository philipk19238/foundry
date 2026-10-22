/-
The pure rules of the foundry catalog, transcribed from the Python:

* `encode`      foundry/core/models.py `encode`: each field is an 8-byte big-endian length, then its bytes.
* `canonFiles`  foundry/catalog/models.py `Listing.files_key`: file digests, deduplicated and sorted.
* `opportunityKey` / `noticeKey`  `Listing.opportunity_id` / `Listing.notice_id` hash these keys with SHA-256.
* `ingest`      foundry/catalog/store.py `add_notices`: INSERT ... ON CONFLICT (notice_id) DO NOTHING.
* `classify`    foundry/catalog/models.py `Change`: unchanged, amendment, or new.
* `verdict`     foundry/health/models.py `Crawl.verdict`.
* `afterCrawl`  foundry/health/models.py `Health.after`.

SHA-256 is outside the model: ids are equal exactly when the encoded keys are equal, up to a SHA-256 collision.
-/

namespace Foundry

abbrev Bytes := List Nat

/-! ## 1. Encoding: a list of fields becomes one byte string, and nothing else encodes to it -/

def header (n : Nat) : Bytes :=
  [n / 72057594037927936 % 256, n / 281474976710656 % 256, n / 1099511627776 % 256, n / 4294967296 % 256,
   n / 16777216 % 256, n / 65536 % 256, n / 256 % 256, n % 256]

def readHeader : Bytes → Option (Nat × Bytes)
  | a :: b :: c :: d :: e :: f :: g :: h :: rest =>
    some (a * 72057594037927936 + b * 281474976710656 + c * 1099511627776 + d * 4294967296
      + e * 16777216 + f * 65536 + g * 256 + h, rest)
  | _ => none

def encodeField (f : Bytes) : Bytes := header f.length ++ f

def encode : List Bytes → Bytes
  | [] => []
  | f :: fs => encodeField f ++ encode fs

def decodeFuel : Nat → Bytes → Option (List Bytes)
  | 0, bs => if bs.isEmpty then some [] else none
  | fuel + 1, bs =>
    if bs.isEmpty then some []
    else
      match readHeader bs with
      | none => none
      | some (n, rest) =>
        if n ≤ rest.length then (decodeFuel fuel (rest.drop n)).map (rest.take n :: ·) else none

def decode (bs : Bytes) : Option (List Bytes) := decodeFuel bs.length bs

/-- Every field is shorter than 2^64 bytes, so its length fits the 8-byte header. -/
def Fits (fs : List Bytes) : Prop := ∀ f ∈ fs, f.length < 18446744073709551616

/-- The encoding the length header replaces: fields glued together. -/
def naive (fs : List Bytes) : Bytes := fs.flatten

theorem readHeader_header (n : Nat) (rest : Bytes) (hn : n < 18446744073709551616) :
    readHeader (header n ++ rest) = some (n, rest) := by
  simp only [header, readHeader, List.cons_append, List.nil_append, Option.some.injEq, Prod.mk.injEq, and_true]
  omega

theorem encode_length_ge (fs : List Bytes) : fs.length ≤ (encode fs).length := by
  induction fs with
  | nil => simp [encode]
  | cons f fs ih => simp [encode, encodeField, header]; omega

theorem decodeFuel_encode :
    ∀ (fs : List Bytes), Fits fs → ∀ k, fs.length ≤ k → decodeFuel k (encode fs) = some fs
  | [], _, k, _ => by cases k <;> simp [encode, decodeFuel]
  | f :: fs, h, k, hk => by
    obtain ⟨j, rfl⟩ : ∃ j, k = j + 1 := ⟨k - 1, by simp at hk; omega⟩
    have hf : f.length < 18446744073709551616 := h f (by simp)
    have hfs : Fits fs := fun g hg => h g (by simp [hg])
    have hshape : encode (f :: fs) = header f.length ++ (f ++ encode fs) := by
      simp [encode, encodeField, List.append_assoc]
    have hrest := decodeFuel_encode fs hfs j (by simp at hk; omega)
    rw [hshape]
    have hne : (header f.length ++ (f ++ encode fs)).isEmpty = false := by simp [header]
    simp only [decodeFuel, hne, Bool.false_eq_true, ite_false, readHeader_header _ _ hf]
    have hle : f.length ≤ (f ++ encode fs).length := by simp
    rw [if_pos hle]
    simp [hrest]

/-- Decoding gives back the fields. -/
theorem decode_encode (fs : List Bytes) (h : Fits fs) : decode (encode fs) = some fs :=
  decodeFuel_encode fs h _ (encode_length_ge fs)

/-- Two different field lists never encode to the same bytes. -/
theorem encode_injective (a b : List Bytes) (ha : Fits a) (hb : Fits b) (heq : encode a = encode b) : a = b := by
  have h := decode_encode a ha
  rw [heq, decode_encode b hb] at h
  exact (Option.some.inj h).symm

/-- Recall check: without the length header, `["ab", "c"]` and `["a", "bc"]` collide. -/
theorem naive_collides : naive [[97, 98], [99]] = naive [[97], [98, 99]] ∧ [[97, 98], [99]] ≠ [[97], [98, 99]] := by
  decide

/-! ## 2. Files: a notice's attachments, in any order and with repeats, give one key -/

def insertSorted (x : Nat) : List Nat → List Nat
  | [] => [x]
  | y :: ys => if x < y then x :: y :: ys else if x = y then y :: ys else y :: insertSorted x ys

def canonFiles (fs : List Nat) : List Nat := fs.foldr insertSorted []

theorem mem_insertSorted (x y : Nat) (ys : List Nat) : y ∈ insertSorted x ys ↔ y = x ∨ y ∈ ys := by
  induction ys with
  | nil => simp [insertSorted]
  | cons z zs ih =>
    unfold insertSorted
    by_cases h1 : x < z
    · simp [h1]
    · by_cases h2 : x = z
      · subst h2; simp [h1]
      · simp [h1, h2, ih, or_left_comm]

theorem mem_canonFiles (x : Nat) (fs : List Nat) : x ∈ canonFiles fs ↔ x ∈ fs := by
  induction fs with
  | nil => simp [canonFiles]
  | cons f fs ih =>
    simp only [canonFiles, List.foldr_cons] at *
    rw [mem_insertSorted, ih]
    simp

theorem sorted_insertSorted (x : Nat) (ys : List Nat) (h : ys.Pairwise (· < ·)) :
    (insertSorted x ys).Pairwise (· < ·) := by
  induction ys with
  | nil => simp [insertSorted]
  | cons z zs ih =>
    rw [List.pairwise_cons] at h
    unfold insertSorted
    by_cases h1 : x < z
    · rw [if_pos h1, List.pairwise_cons]
      refine ⟨?_, List.pairwise_cons.mpr h⟩
      intro a ha
      simp at ha
      rcases ha with rfl | ha
      · exact h1
      · exact Nat.lt_trans h1 (h.1 a ha)
    · by_cases h2 : x = z
      · subst h2
        rw [if_neg h1, if_pos rfl]
        exact List.pairwise_cons.mpr h
      · rw [if_neg h1, if_neg h2, List.pairwise_cons]
        refine ⟨?_, ih h.2⟩
        intro a ha
        rw [mem_insertSorted] at ha
        rcases ha with rfl | ha
        · omega
        · exact h.1 a ha

theorem sorted_canonFiles (fs : List Nat) : (canonFiles fs).Pairwise (· < ·) := by
  induction fs with
  | nil => simp [canonFiles]
  | cons f fs ih => exact sorted_insertSorted f _ ih

theorem eq_of_sorted_same_members :
    ∀ (a b : List Nat), a.Pairwise (· < ·) → b.Pairwise (· < ·) → (∀ x, x ∈ a ↔ x ∈ b) → a = b
  | [], [], _, _, _ => rfl
  | [], y :: _, _, _, h => absurd ((h y).mpr (by simp)) (by simp)
  | x :: _, [], _, _, h => absurd ((h x).mp (by simp)) (by simp)
  | x :: xs, y :: ys, ha, hb, h => by
    rw [List.pairwise_cons] at ha hb
    have hxy : x = y := by
      have hx : x ∈ y :: ys := (h x).mp (by simp)
      have hy : y ∈ x :: xs := (h y).mpr (by simp)
      simp at hx hy
      rcases hx with hx | hx
      · exact hx
      · rcases hy with hy | hy
        · exact hy.symm
        · have := hb.1 x hx; have := ha.1 y hy; omega
    subst hxy
    have htail : ∀ z, z ∈ xs ↔ z ∈ ys := by
      intro z
      constructor
      · intro hz
        have hne : z ≠ x := fun e => by subst e; exact Nat.lt_irrefl _ (ha.1 _ hz)
        have := (h z).mp (by simp [hz])
        simp [hne] at this
        exact this
      · intro hz
        have hne : z ≠ x := fun e => by subst e; exact Nat.lt_irrefl _ (hb.1 _ hz)
        have := (h z).mpr (by simp [hz])
        simp [hne] at this
        exact this
    rw [eq_of_sorted_same_members xs ys ha.2 hb.2 htail]

/-- Two attachment lists give the same canonical list exactly when they hold the same files. -/
theorem canonFiles_eq_iff (a b : List Nat) : canonFiles a = canonFiles b ↔ ∀ x, x ∈ a ↔ x ∈ b := by
  constructor
  · intro h x
    rw [← mem_canonFiles x a, ← mem_canonFiles x b, h]
  · intro h
    apply eq_of_sorted_same_members _ _ (sorted_canonFiles a) (sorted_canonFiles b)
    intro x
    rw [mem_canonFiles, mem_canonFiles]
    exact h x

theorem map_injective (f : Nat → Bytes) (hf : ∀ x y, f x = f y → x = y) :
    ∀ (a b : List Nat), a.map f = b.map f → a = b
  | [], [], _ => rfl
  | [], _ :: _, h => by simp at h
  | _ :: _, [], h => by simp at h
  | x :: xs, y :: ys, h => by
    simp only [List.map_cons, List.cons.injEq] at h
    rw [hf x y h.1, map_injective f hf xs ys h.2]

/-! ## 3. Identity: opportunity_id from static content, notice_id from everything -/

structure Listing where
  source : Bytes
  externalId : Bytes
  title : Bytes
  dueAt : Bytes
  description : Bytes
  metadata : Bytes
  files : List Nat
  deriving DecidableEq, Repr

def tagOpportunity : Bytes := [111, 112, 112, 111, 114, 116, 117, 110, 105, 116, 121]
def tagNotice : Bytes := [110, 111, 116, 105, 99, 101]

/-- The static part of a listing: which portal, and the portal's own id for it. -/
def opportunityKey (l : Listing) : List Bytes := [tagOpportunity, l.source, l.externalId]

/-- Everything a portal can change between two scrapes of one opportunity. -/
def noticeKey (digest : Nat → Bytes) (l : Listing) : List Bytes :=
  [tagNotice, l.source, l.externalId, l.title, l.dueAt, l.description, l.metadata]
    ++ (canonFiles l.files).map digest

/-- Same files, same content: the files' members, not their order or repeats. -/
def SameContent (a b : Listing) : Prop :=
  a.source = b.source ∧ a.externalId = b.externalId ∧ a.title = b.title ∧ a.dueAt = b.dueAt
    ∧ a.description = b.description ∧ a.metadata = b.metadata ∧ ∀ x, x ∈ a.files ↔ x ∈ b.files

/-- A portal edit to the title, due date, description, metadata or files never moves the opportunity. -/
theorem opportunity_ignores_dynamic (a b : Listing) (hs : a.source = b.source) (he : a.externalId = b.externalId) :
    encode (opportunityKey a) = encode (opportunityKey b) := by
  simp [opportunityKey, hs, he]

/-- Two scrapes share an opportunity id exactly when they come from the same portal record. -/
theorem opportunity_same_iff (a b : Listing) (ha : Fits (opportunityKey a)) (hb : Fits (opportunityKey b)) :
    encode (opportunityKey a) = encode (opportunityKey b) ↔ a.source = b.source ∧ a.externalId = b.externalId := by
  constructor
  · intro h
    have := encode_injective _ _ ha hb h
    simp [opportunityKey] at this
    exact this
  · rintro ⟨hs, he⟩
    exact opportunity_ignores_dynamic a b hs he

/-- Two scrapes share a notice id exactly when they have the same content; file order and repeats don't count. -/
theorem notice_same_iff (digest : Nat → Bytes) (hd : ∀ x y, digest x = digest y → x = y) (a b : Listing)
    (ha : Fits (noticeKey digest a)) (hb : Fits (noticeKey digest b)) :
    encode (noticeKey digest a) = encode (noticeKey digest b) ↔ SameContent a b := by
  constructor
  · intro h
    have hk := encode_injective _ _ ha hb h
    simp only [noticeKey, List.cons_append, List.cons.injEq, List.nil_append] at hk
    obtain ⟨-, hs, he, ht, hdue, hdesc, hm, hf⟩ := hk
    have hfiles : canonFiles a.files = canonFiles b.files := map_injective digest hd _ _ hf
    exact ⟨hs, he, ht, hdue, hdesc, hm, (canonFiles_eq_iff _ _).mp hfiles⟩
  · rintro ⟨hs, he, ht, hdue, hdesc, hm, hf⟩
    have hfiles := (canonFiles_eq_iff _ _).mpr hf
    simp [noticeKey, hs, he, ht, hdue, hdesc, hm, hfiles]

/-- A notice belongs to one opportunity: the same notice id means the same opportunity id. -/
theorem notice_determines_opportunity (digest : Nat → Bytes) (hd : ∀ x y, digest x = digest y → x = y)
    (a b : Listing) (ha : Fits (noticeKey digest a)) (hb : Fits (noticeKey digest b))
    (h : encode (noticeKey digest a) = encode (noticeKey digest b)) :
    encode (opportunityKey a) = encode (opportunityKey b) := by
  obtain ⟨hs, he, -⟩ := (notice_same_iff digest hd a b ha hb).mp h
  exact opportunity_ignores_dynamic a b hs he

/-! ## 4. Ingest: storing scraped notices is a set union, so re-runs and reordering change nothing -/

def ingest (catalog : List Bytes) (n : Bytes) : List Bytes := if n ∈ catalog then catalog else catalog ++ [n]

def ingestAll (catalog ns : List Bytes) : List Bytes := ns.foldl ingest catalog

theorem mem_ingest (x n : Bytes) (c : List Bytes) : x ∈ ingest c n ↔ x ∈ c ∨ x = n := by
  unfold ingest
  by_cases h : n ∈ c
  · simp only [h, if_true]
    constructor
    · exact Or.inl
    · rintro (hx | rfl)
      · exact hx
      · exact h
  · simp [h]

theorem mem_ingestAll (x : Bytes) : ∀ (c ns : List Bytes), x ∈ ingestAll c ns ↔ x ∈ c ∨ x ∈ ns
  | c, [] => by simp [ingestAll]
  | c, n :: ns => by
    have := mem_ingestAll x (ingest c n) ns
    simp only [ingestAll, List.foldl_cons] at *
    rw [this, mem_ingest]
    simp [or_assoc]

theorem nodup_ingestAll : ∀ (c ns : List Bytes), c.Nodup → (ingestAll c ns).Nodup
  | c, [], h => by simpa [ingestAll] using h
  | c, n :: ns, h => by
    have hn : (ingest c n).Nodup := by
      unfold ingest
      by_cases hm : n ∈ c
      · simpa [hm] using h
      · simp only [hm, if_false]
        rw [List.nodup_append]
        simp [h, hm]
        intro a ha e
        exact hm (e ▸ ha)
    simpa [ingestAll] using nodup_ingestAll (ingest c n) ns hn

/-- Running a refresh again adds nothing. -/
theorem ingestAll_idempotent (c ns : List Bytes) : ingestAll (ingestAll c ns) ns = ingestAll c ns := by
  suffices h : ∀ (d : List Bytes) (ms : List Bytes), (∀ m ∈ ms, m ∈ d) → ingestAll d ms = d by
    apply h
    intro m hm
    exact (mem_ingestAll m c ns).mpr (Or.inr hm)
  intro d ms
  induction ms with
  | nil => intro; rfl
  | cons m ms ih =>
    intro hall
    have hm : m ∈ d := hall m (by simp)
    simp only [ingestAll, List.foldl_cons, ingest, hm, if_true]
    exact ih (fun x hx => hall x (by simp [hx]))

/-- Two refreshes that scrape the same notices in different orders store the same set. -/
theorem ingestAll_order_free (c ns ms : List Bytes) (h : ∀ x, x ∈ ns ↔ x ∈ ms) (x : Bytes) :
    x ∈ ingestAll c ns ↔ x ∈ ingestAll c ms := by
  rw [mem_ingestAll, mem_ingestAll, h]

/-- A row, once stored, stays: ingest never drops or rewrites a notice. -/
theorem ingestAll_keeps (c ns : List Bytes) (x : Bytes) (hx : x ∈ c) : x ∈ ingestAll c ns :=
  (mem_ingestAll x c ns).mpr (Or.inl hx)

inductive Change
  | unchanged
  | amendment
  | new
  deriving DecidableEq, Repr

def classify (notices opportunities : List Bytes) (noticeId opportunityId : Bytes) : Change :=
  if noticeId ∈ notices then .unchanged else if opportunityId ∈ opportunities then .amendment else .new

theorem classify_unchanged_iff (ns os : List Bytes) (n o : Bytes) : classify ns os n o = .unchanged ↔ n ∈ ns := by
  unfold classify
  by_cases h : n ∈ ns <;> by_cases h' : o ∈ os <;> simp [h, h']

theorem classify_amendment_iff (ns os : List Bytes) (n o : Bytes) :
    classify ns os n o = .amendment ↔ n ∉ ns ∧ o ∈ os := by
  unfold classify
  by_cases h : n ∈ ns <;> by_cases h' : o ∈ os <;> simp [h, h']

/-! ## 5. Health: after a crawl, is the scraper fine, logged out, or broken? -/

structure Crawl where
  authFailed : Bool
  crashed : Bool
  invalid : Nat
  valid : Nat
  previousValid : Nat
  deriving DecidableEq, Repr

inductive Verdict
  | healthy
  | relogin
  | heal
  deriving DecidableEq, Repr

def verdict (c : Crawl) : Verdict :=
  if c.authFailed then .relogin
  else if c.crashed || decide (c.invalid > 0) || (decide (c.valid = 0) && decide (c.previousValid > 0)) then .heal
  else .healthy

/-- The verdict without the silent-breakage gate. -/
def naiveVerdict (c : Crawl) : Verdict :=
  if c.authFailed then .relogin else if c.crashed || decide (c.invalid > 0) then .heal else .healthy

inductive Status
  | ready
  | relogin
  | healing
  | needsHuman
  deriving DecidableEq, Repr

structure Health where
  status : Status
  heals : Nat
  deriving DecidableEq, Repr

def maxHeals : Nat := 3

/-- Relogins count toward the same budget as heals: a portal that refuses every fresh login would otherwise loop
relogin, refresh, relogin forever (`naiveAfterCrawl`). -/
def afterCrawl (h : Health) (c : Crawl) : Health :=
  match verdict c with
  | .healthy => ⟨.ready, 0⟩
  | .relogin => if h.heals < maxHeals then ⟨.relogin, h.heals + 1⟩ else ⟨.needsHuman, h.heals⟩
  | .heal => if h.heals < maxHeals then ⟨.healing, h.heals + 1⟩ else ⟨.needsHuman, h.heals⟩

/-- The shipped-first draft: a relogin never spends the budget. -/
def naiveAfterCrawl (h : Health) (c : Crawl) : Health :=
  match verdict c with
  | .healthy => ⟨.ready, 0⟩
  | .relogin => ⟨.relogin, h.heals⟩
  | .heal => if h.heals < maxHeals then ⟨.healing, h.heals + 1⟩ else ⟨.needsHuman, h.heals⟩

def loggedOut : Crawl := ⟨true, false, 0, 0, 12⟩

/-- Recall check: under the draft, a portal that always logs the scraper out never reaches a person. -/
theorem naive_relogin_loops_forever (n : Nat) :
    (Nat.repeat (fun h => naiveAfterCrawl h loggedOut) n ⟨.ready, 0⟩).status ≠ .needsHuman := by
  induction n with
  | zero => decide
  | succ n ih =>
    simp only [Nat.repeat]
    simp [naiveAfterCrawl, verdict, loggedOut]

/-- A portal that keeps refusing logins reaches a person after `maxHeals` relogins. -/
theorem relogin_loop_reaches_human :
    (Nat.repeat (fun h => afterCrawl h loggedOut) (maxHeals + 1) ⟨.ready, 0⟩).status = .needsHuman := by
  decide

theorem logged_out_relogs (c : Crawl) (h : c.authFailed = true) : verdict c = .relogin := by
  simp [verdict, h]

/-- A scraper that used to find notices and now finds none is broken, even with no error. -/
theorem silent_breakage_heals (c : Crawl) (ha : c.authFailed = false) (hv : c.valid = 0) (hp : c.previousValid > 0) :
    verdict c = .heal := by
  simp [verdict, ha, hv, hp]

/-- A clean crawl is healthy: no crash, no invalid item, and notices found or none expected. -/
theorem clean_is_healthy (c : Crawl) (ha : c.authFailed = false) (hc : c.crashed = false) (hi : c.invalid = 0)
    (hv : c.valid > 0 ∨ c.previousValid = 0) : verdict c = .healthy := by
  unfold verdict
  rcases hv with hv | hv
  · simp [ha, hc, hi, Nat.pos_iff_ne_zero.mp hv]
  · simp [ha, hc, hi, hv]

/-- Recall check: without the silent-breakage gate, a portal redesign that returns nothing looks healthy. -/
theorem naive_misses_silent_breakage :
    naiveVerdict ⟨false, false, 0, 0, 12⟩ = .healthy ∧ verdict ⟨false, false, 0, 0, 12⟩ = .heal := by
  decide

/-- Healing never runs more than `maxHeals` times in a row. -/
theorem heals_bounded (h : Health) (c : Crawl) (hb : h.heals ≤ maxHeals) : (afterCrawl h c).heals ≤ maxHeals := by
  unfold afterCrawl
  split
  · simp
  · split
    · simp; omega
    · simpa
  · split
    · simp; omega
    · simpa

/-- A person is asked only after the repairs ran out, and never for a healthy crawl. -/
theorem needsHuman_only_when_exhausted (h : Health) (c : Crawl) (hn : (afterCrawl h c).status = .needsHuman) :
    verdict c ≠ .healthy ∧ maxHeals ≤ h.heals := by
  cases hv : verdict c with
  | healthy => simp [afterCrawl, hv] at hn
  | relogin =>
    refine ⟨by simp, ?_⟩
    simp only [afterCrawl, hv] at hn
    split at hn
    · simp at hn
    · omega
  | heal =>
    refine ⟨by simp, ?_⟩
    simp only [afterCrawl, hv] at hn
    split at hn
    · simp at hn
    · omega

/-- One healthy crawl clears the heal count. -/
theorem healthy_resets (h : Health) (c : Crawl) (hv : verdict c = .healthy) : afterCrawl h c = ⟨.ready, 0⟩ := by
  simp [afterCrawl, hv]

/-! ## 6. Bounded searches and differential vectors -/

#guard (List.range 6).all fun n => (List.range 6).all fun m =>
  decode (encode [List.replicate n 7, List.replicate m 9]) == some [List.replicate n 7, List.replicate m 9]

#guard canonFiles [3, 1, 3, 2, 1] == [1, 2, 3]
#guard canonFiles [2, 1, 3] == canonFiles [3, 2, 1, 1]

#guard (List.range 4).all fun heals => [true, false].all fun a => [true, false].all fun cr =>
  (List.range 2).all fun inv => (List.range 2).all fun v => (List.range 2).all fun p =>
    (afterCrawl ⟨.ready, heals⟩ ⟨a, cr, inv, v, p⟩).heals ≤ maxHeals

def utf8 (s : String) : Bytes := s.toUTF8.toList.map (·.toNat)

def hexDigit (d : Nat) : Nat := if d < 10 then 48 + d else 87 + d

def hexField (n : Nat) : Bytes := ((List.range 64).reverse.map fun i => hexDigit (n / 16 ^ i % 16))

def hex (bs : Bytes) : String :=
  String.join (bs.map fun b => String.ofList [Char.ofNat (hexDigit (b / 16)), Char.ofNat (hexDigit (b % 16))])

def sample : Listing :=
  { source := utf8 "6f1c2a9e-0b7d-4e55-9a7e-1d2f3c4b5a69", externalId := utf8 "RFP-25-001", title := utf8 "Road salt",
    dueAt := utf8 "2026-11-03T14:00:00-06:00",
    description := utf8 "Bulk road salt", metadata := utf8 "{\"naics\":\"212390\"}", files := [
      59126477157097212714377166449681610041871851358233539270424794581628420598807,
      48919551711525898028777735810013705148162447905680889003658162376953041795265,
      59126477157097212714377166449681610041871851358233539270424794581628420598807] }

#eval IO.println s!"VECTOR opportunity {hex (encode (opportunityKey sample))}"
#eval IO.println s!"VECTOR notice {hex (encode (noticeKey hexField sample))}"
#eval IO.println s!"VECTOR verdict 0,0,0,0,12 {repr (verdict ⟨false, false, 0, 0, 12⟩)}"
#eval IO.println s!"VECTOR after 3,relogin {repr (afterCrawl ⟨.relogin, 3⟩ loggedOut)}"

#print axioms encode_injective
#print axioms notice_same_iff
#print axioms ingestAll_idempotent
#print axioms heals_bounded

end Foundry
