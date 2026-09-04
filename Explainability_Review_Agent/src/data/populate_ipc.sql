CREATE TABLE ipc_standards (
    id SERIAL PRIMARY KEY,
    package_type VARCHAR(50),      -- e.g., '0402', 'QFN', 'BGA'
    class_level VARCHAR(20),       -- e.g., 'Class 2', 'Class 3'
    max_side_overhang VARCHAR(100),
    min_solder_thickness VARCHAR(100),
    max_tilt_angle_deg FLOAT,
    notes TEXT
);

INSERT INTO ipc_standards (package_type, class_level, max_side_overhang, min_solder_thickness, max_tilt_angle_deg, notes)
VALUES 
  ('0402', 'Class 3', '0% (No overhang permitted)', '50um', 3.0, 'Mandatory for aerospace/medical'),
  ('0603', 'Class 2', 'Max 25% pad width overhang', '75um', 5.0, 'Standard commercial electronics'),
  ('QFN',  'Class 3', 'Toe overhang must not exceed 25%', '100um', 1.0, 'Voiding under thermal pad < 15%');
