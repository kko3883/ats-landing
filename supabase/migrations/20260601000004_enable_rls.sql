-- Enable RLS and create read-only policies for all three ATS tables
-- Engine writes via service_role (bypasses RLS), dashboard reads via anon

-- 1. signals
ALTER TABLE signals ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_signals" ON signals;
CREATE POLICY "anon_select_signals" ON signals
  FOR SELECT
  TO anon
  USING (true);

-- 2. orders
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_orders" ON orders;
CREATE POLICY "anon_select_orders" ON orders
  FOR SELECT
  TO anon
  USING (true);

-- 3. portfolio
ALTER TABLE portfolio ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "anon_select_portfolio" ON portfolio;
CREATE POLICY "anon_select_portfolio" ON portfolio
  FOR SELECT
  TO anon
  USING (true);
