-- Reference data (synthetic). Products, benchmark history, holidays.
INSERT INTO lms.loan_product VALUES
 -- code     name                      day_count grace penal  flat    pct    cap      prepay  npa
 ('HL-STD', 'Home Loan Standard',      'ACT365', 5,  2.000,  500.00, 0.500, 5000.00, 2.000, 90),
 ('PL-FLX', 'Personal Loan Flex',      '30360',  3,  3.500,  300.00, 1.000, 2500.00, 2.000, 90),
 ('AL-ST',  'Auto Loan Short Term',    'ACT360', 0,  2.500,  250.00, NULL,  NULL,    1.500, 90),
 ('BL-MSM', 'Business Loan MSME',      'ACT365', 7,  3.000,  NULL,   0.750, 10000.00, 2.000, 90);

INSERT INTO lms.benchmark_rate VALUES
 ('REPO', '2022-12-07', 6.2500),
 ('REPO', '2023-02-08', 6.5000),
 ('REPO', '2024-02-08', 6.5000),
 ('REPO', '2024-10-09', 6.7500),
 ('REPO', '2025-04-09', 6.2500),
 ('REPO', '2025-10-09', 5.7500);

-- 2024 and 2025 holidays, a handful only
INSERT INTO lms.holiday VALUES
 ('2024-01-26', 'ALL', 'Republic Day'),
 ('2024-03-25', 'ALL', 'Holi'),
 ('2024-08-15', 'ALL', 'Independence Day'),
 ('2024-10-02', 'ALL', 'Gandhi Jayanti'),
 ('2024-11-01', 'KA',  'Kannada Rajyotsava'),
 ('2024-12-25', 'ALL', 'Christmas'),
 ('2025-01-26', 'ALL', 'Republic Day'),
 ('2025-03-14', 'ALL', 'Holi'),
 ('2025-04-14', 'ALL', 'Ambedkar Jayanti'),
 ('2025-08-15', 'ALL', 'Independence Day'),
 ('2025-10-02', 'ALL', 'Gandhi Jayanti'),
 ('2025-11-01', 'KA',  'Kannada Rajyotsava'),
 ('2025-12-25', 'ALL', 'Christmas');
