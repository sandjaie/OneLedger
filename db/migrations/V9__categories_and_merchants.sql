-- db/migrations/V9__categories_and_merchants.sql
-- M1b Task 12: the classification reference data (schema.md 0006) and the seed taxonomy
-- (classification.html section 3). Two reconciliations against the reference shape, recorded
-- in schema.md in this same commit:
--   * 0006's max_two_levels CHECK carried a subquery, which PostgreSQL rejects in CHECK
--     constraints; the two-level rule is a trigger here, enforced in both directions.
--   * system slugs are unique so the seed is idempotent (ON CONFLICT DO NOTHING) and a later
--     seed-update migration can re-run the set without duplicating rows.
-- The taxonomy seeds plain names only. The regional brand names in the design document are
-- seed-pack examples (rows, arriving with M4's rule packs), never category names (ADR-0012).

CREATE TYPE category_kind   AS ENUM ('income','expense','transfer','investment','debt');
CREATE TYPE category_bucket AS ENUM ('need','want','investment','debt_service','income','ignore');

CREATE TABLE category (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id UUID REFERENCES household(id),      -- NULL = system seed
  parent_id    UUID REFERENCES category(id),
  name         TEXT NOT NULL,
  slug         TEXT NOT NULL,
  kind         category_kind   NOT NULL,
  bucket       category_bucket NOT NULL,
  icon         TEXT,
  color        TEXT,
  sort_order   SMALLINT NOT NULL DEFAULT 0,
  is_system    BOOLEAN NOT NULL DEFAULT false,
  is_active    BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX category_system_slug    ON category (slug) WHERE household_id IS NULL;
CREATE UNIQUE INDEX category_household_slug ON category (household_id, slug)
  WHERE household_id IS NOT NULL;
CREATE INDEX category_parent ON category (parent_id) WHERE parent_id IS NOT NULL;

-- The tree is two levels, in both directions: a child of a child is rejected, and a category
-- that has children cannot itself become a child (re-parenting it would push its children to a
-- third level from below).
CREATE FUNCTION assert_category_two_levels() RETURNS TRIGGER
  LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.parent_id IS NOT NULL THEN
    IF EXISTS (SELECT FROM category p WHERE p.id = NEW.parent_id AND p.parent_id IS NOT NULL) THEN
      RAISE EXCEPTION 'the category tree is two levels: % cannot be a child of a child', NEW.slug;
    END IF;
    IF EXISTS (SELECT FROM category c WHERE c.parent_id = NEW.id) THEN
      RAISE EXCEPTION 'the category tree is two levels: % has children and cannot become a child', NEW.slug;
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER trg_category_two_levels
  BEFORE INSERT OR UPDATE OF parent_id ON category
  FOR EACH ROW EXECUTE FUNCTION assert_category_two_levels();

CREATE TABLE merchant (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  household_id        UUID REFERENCES household(id),   -- NULL = system seed
  canonical_name      TEXT NOT NULL,
  display_name        TEXT,
  default_category_id UUID REFERENCES category(id),
  country             CHAR(2),
  merchant_type       TEXT,
  logo_url            TEXT,
  is_system           BOOLEAN NOT NULL DEFAULT false,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TYPE pattern_type AS ENUM ('exact','prefix','contains','regex');

CREATE TABLE merchant_alias (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  merchant_id   UUID NOT NULL REFERENCES merchant(id) ON DELETE CASCADE,
  household_id  UUID REFERENCES household(id),
  pattern       TEXT NOT NULL,
  pattern_kind  pattern_type NOT NULL DEFAULT 'contains',
  priority      SMALLINT NOT NULL DEFAULT 100,
  is_active     BOOLEAN NOT NULL DEFAULT true,
  times_matched INTEGER NOT NULL DEFAULT 0,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Isolation follows the institution pattern (V2): the shared seed set is readable, only the
-- household's own rows are writable, and anon with no bound user sees only the seed set.
ALTER TABLE category       ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchant       ENABLE ROW LEVEL SECURITY;
ALTER TABLE merchant_alias ENABLE ROW LEVEL SECURITY;

CREATE POLICY category_read ON category FOR SELECT
  USING (household_id IS NULL OR household_id = current_household_id());
CREATE POLICY category_write ON category FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

CREATE POLICY merchant_read ON merchant FOR SELECT
  USING (household_id IS NULL OR household_id = current_household_id());
CREATE POLICY merchant_write ON merchant FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

CREATE POLICY merchant_alias_read ON merchant_alias FOR SELECT
  USING (household_id IS NULL OR household_id = current_household_id());
CREATE POLICY merchant_alias_write ON merchant_alias FOR ALL
  USING (household_id = current_household_id())
  WITH CHECK (household_id = current_household_id());

-- The seed taxonomy: classification.html section 3, verbatim tree, plain names. A child
-- starts with its parent's kind and bucket as the editable default (ADR-0008).
INSERT INTO category (household_id, name, slug, kind, bucket, sort_order, is_system)
SELECT NULL, v.name, v.slug, v.kind::category_kind, v.bucket::category_bucket, v.ord, true
FROM (VALUES
  -- Income
  ('Salary',               'salary',             'income',     'income',       10),
  ('Bonus',                'bonus',              'income',     'income',       20),
  ('Business / Freelance', 'business-freelance', 'income',     'income',       30),
  ('Investment income',    'investment-income',  'income',     'income',       40),
  ('Other income',         'other-income',       'income',     'income',       50),
  -- Expenses
  ('Housing',              'housing',            'expense',    'need',        110),
  ('Utilities',            'utilities',          'expense',    'need',        120),
  ('Groceries',            'groceries',          'expense',    'need',        130),
  ('Food & Dining',        'food-dining',        'expense',    'want',        140),
  ('Transport',            'transport',          'expense',    'need',        150),
  ('Health',               'health',             'expense',    'need',        160),
  ('Education',            'education',          'expense',    'need',        170),
  ('Family',               'family',             'expense',    'need',        180),
  ('Shopping',             'shopping',           'expense',    'want',        190),
  ('Entertainment',        'entertainment',      'expense',    'want',        200),
  ('Travel',               'travel',             'expense',    'want',        210),
  ('Subscriptions',        'subscriptions',      'expense',    'want',        220),
  ('Financial',            'financial',          'expense',    'need',        230),
  ('Insurance',            'insurance',          'expense',    'need',        240),
  ('Taxes',                'taxes',              'expense',    'need',        250),
  ('Charity',              'charity',            'expense',    'want',        260),
  ('Personal',             'personal',           'expense',    'want',        270),
  ('Uncategorized',        'uncategorized',      'expense',    'ignore',      280),
  -- Non-consumption
  ('Investments',          'investments',        'investment', 'investment',  310),
  ('Debt service',         'debt-service',       'debt',       'debt_service',320),
  ('Transfers',            'transfers',          'transfer',   'ignore',      330)
) AS v(name, slug, kind, bucket, ord)
ON CONFLICT (slug) WHERE household_id IS NULL DO NOTHING;

INSERT INTO category (household_id, parent_id, name, slug, kind, bucket, sort_order, is_system)
SELECT NULL, p.id, v.name, p.slug || '-' || v.slug, p.kind, p.bucket, v.ord, true
FROM (VALUES
  ('salary',            'Base',                 'base',                10),
  ('salary',            'Allowances',           'allowances',          20),
  ('salary',            'Overtime',             'overtime',            30),
  ('salary',            'Arrears',              'arrears',             40),
  ('bonus',             'Annual',               'annual',              10),
  ('bonus',             'Performance',          'performance',         20),
  ('bonus',             'Joining',              'joining',             30),
  ('investment-income', 'Dividend',             'dividend',            10),
  ('investment-income', 'Interest',             'interest',            20),
  ('investment-income', 'Capital gains',        'capital-gains',       30),
  ('investment-income', 'Rental',               'rental',              40),
  ('other-income',      'Gifts received',       'gifts-received',      10),
  ('other-income',      'Other income',         'other',               20),
  ('housing',           'Rent',                 'rent',                10),
  ('housing',           'Mortgage interest',    'mortgage-interest',   20),
  ('housing',           'Service charge',       'service-charge',      30),
  ('housing',           'Maintenance',          'maintenance',         40),
  ('housing',           'Home insurance',       'home-insurance',      50),
  ('utilities',         'Electricity & water',  'electricity-water',   10),
  ('utilities',         'Gas',                  'gas',                 20),
  ('utilities',         'Cooling',              'cooling',             30),
  ('utilities',         'Internet',             'internet',            40),
  ('utilities',         'Mobile',               'mobile',              50),
  ('utilities',         'Cable & DTH',          'cable-dth',           60),
  ('groceries',         'Supermarket',          'supermarket',         10),
  ('groceries',         'Fresh produce',        'fresh-produce',       20),
  ('groceries',         'Household supplies',   'household-supplies',  30),
  ('food-dining',       'Restaurants',          'restaurants',         10),
  ('food-dining',       'Coffee',               'coffee',              20),
  ('food-dining',       'Delivery',             'delivery',            30),
  ('food-dining',       'Takeaway',             'takeaway',            40),
  ('food-dining',       'Workplace',            'workplace',           50),
  ('transport',         'Fuel',                 'fuel',                10),
  ('transport',         'Taxi',                 'taxi',                20),
  ('transport',         'Tolls',                'tolls',               30),
  ('transport',         'Parking',              'parking',             40),
  ('transport',         'Public transport',     'public-transport',    50),
  ('transport',         'Vehicle maintenance',  'vehicle-maintenance', 60),
  ('transport',         'Registration',         'registration',        70),
  ('transport',         'Motor insurance',      'motor-insurance',     80),
  ('health',            'Doctor',               'doctor',              10),
  ('health',            'Dentist',              'dentist',             20),
  ('health',            'Pharmacy',             'pharmacy',            30),
  ('health',            'Health insurance',     'health-insurance',    40),
  ('health',            'Optical',              'optical',             50),
  ('health',            'Lab tests',            'lab-tests',           60),
  ('education',         'School fees',          'school-fees',         10),
  ('education',         'Tuition',              'tuition',             20),
  ('education',         'Courses',              'courses',             30),
  ('education',         'Books & supplies',     'books-supplies',      40),
  ('family',            'Childcare',            'childcare',           10),
  ('family',            'Domestic help',        'domestic-help',       20),
  ('family',            'Family support',       'family-support',      30),
  ('family',            'Elder care',           'elder-care',          40),
  ('shopping',          'Clothing',             'clothing',            10),
  ('shopping',          'Electronics',          'electronics',         20),
  ('shopping',          'Home & furniture',     'home-furniture',      30),
  ('shopping',          'Personal care',        'personal-care',       40),
  ('shopping',          'Gifts',                'gifts',               50),
  ('entertainment',     'Streaming',            'streaming',           10),
  ('entertainment',     'Cinema',               'cinema',              20),
  ('entertainment',     'Events',               'events',              30),
  ('entertainment',     'Hobbies',              'hobbies',             40),
  ('entertainment',     'Sports & gym',         'sports-gym',          50),
  ('travel',            'Flights',              'flights',             10),
  ('travel',            'Hotels',               'hotels',              20),
  ('travel',            'Holiday',              'holiday',             30),
  ('travel',            'Visa & immigration',   'visa-immigration',    40),
  ('subscriptions',     'Software',             'software',            10),
  ('subscriptions',     'Cloud storage',        'cloud-storage',       20),
  ('subscriptions',     'News & media',         'news-media',          30),
  ('subscriptions',     'Memberships',          'memberships',         40),
  ('financial',         'Bank charges',         'bank-charges',        10),
  ('financial',         'Card fees',            'card-fees',           20),
  ('financial',         'FX & remittance fees', 'fx-remittance-fees',  30),
  ('financial',         'Late fees',            'late-fees',           40),
  ('financial',         'Advisory',             'advisory',            50),
  ('insurance',         'Life',                 'life',                10),
  ('insurance',         'Health',               'health',              20),
  ('insurance',         'Home',                 'home',                30),
  ('insurance',         'Travel',               'travel',              40),
  ('insurance',         'Motor',                'motor',               50),
  ('taxes',             'Income tax',           'income-tax',          10),
  ('taxes',             'VAT',                  'vat',                 20),
  ('taxes',             'Property tax',         'property-tax',        30),
  ('taxes',             'Other',                'other',               40),
  ('charity',           'Zakat',                'zakat',               10),
  ('charity',           'Donations',            'donations',           20),
  ('charity',           'Religious',            'religious',           30),
  ('personal',          'Grooming',             'grooming',            10),
  ('personal',          'Salon',                'salon',               20),
  ('personal',          'Miscellaneous',        'miscellaneous',       30),
  ('investments',       'Mutual fund purchase', 'mutual-fund',         10),
  ('investments',       'SIP',                  'sip',                 20),
  ('investments',       'Equity purchase',      'equity',              30),
  ('investments',       'Gold',                 'gold',                40),
  ('investments',       'PPF',                  'ppf',                 50),
  ('investments',       'NPS',                  'nps',                 60),
  ('investments',       'FD',                   'fd',                  70),
  ('investments',       'International',        'international',       80),
  ('debt-service',      'Loan EMI',             'loan-emi',            10),
  ('debt-service',      'Loan prepayment',      'loan-prepayment',     20),
  ('debt-service',      'Credit card payment',  'card-payment',        30),
  ('debt-service',      'Interest paid',        'interest-paid',       40),
  ('transfers',         'Internal',             'internal',            10),
  ('transfers',         'Remittance',           'remittance',          20),
  ('transfers',         'Cash withdrawal',      'cash-withdrawal',     30),
  ('transfers',         'To family',            'to-family',           40)
) AS v(parent_slug, name, slug, ord)
JOIN category p ON p.household_id IS NULL AND p.slug = v.parent_slug
ON CONFLICT (slug) WHERE household_id IS NULL DO NOTHING;
