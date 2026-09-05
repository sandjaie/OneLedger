-- db/seeds/institutions.sql
INSERT INTO institution (household_id, name, short_name, country, institution_type) VALUES
  (NULL, 'Emirates NBD',   'ENBD',  'AE', 'bank'),
  (NULL, 'HDFC Bank',      'HDFC',  'IN', 'bank'),
  (NULL, 'Axis Bank',      'Axis',  'IN', 'bank'),
  (NULL, 'Wise',           'Wise',  'AE', 'exchange_house'),
  (NULL, 'Careem Pay',     'Careem','AE', 'exchange_house'),
  (NULL, 'ETMoney',        'ETM',   'IN', 'broker'),
  (NULL, 'Kuvera',         'Kuvera','IN', 'broker'),
  (NULL, 'CAMS',           'CAMS',  'IN', 'other'),
  (NULL, 'KFintech',       'KFin',  'IN', 'other');
