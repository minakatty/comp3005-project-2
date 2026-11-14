SELECT t.did, t.n FROM (SELECT did, COUNT(*) AS n FROM Emp GROUP BY did) AS t JOIN Dept d ON t.did = d.did WHERE d.budget > 30000;
