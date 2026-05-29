SELECT p.person_id, m.value_as_number, m.measurement_date
FROM person p
JOIN measurement m ON p.person_id = m.person_id
WHERE m.measurement_concept_id = 3016723
  AND m.measurement_date BETWEEN DATE '2024-01-01' AND DATE '2024-03-31'
