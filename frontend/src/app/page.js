"use client";

import { useEffect, useState } from 'react';

export default function Home() {
  const [articles, setArticles] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/news')
      .then(res => res.json())
      .then(data => {
        if (data.success) {
          setArticles(data.articles);
        }
        setLoading(false);
      })
      .catch(err => {
        console.error("Failed to fetch news", err);
        setLoading(false);
      });
  }, []);

  if (loading) {
    return <div className="loading">Loading Financial Intelligence...</div>;
  }

  // Calculate high level stats
  const total = articles.length;
  const positiveCount = articles.filter(a => a.sentiment_label === 'Positive').length;
  const negativeCount = articles.filter(a => a.sentiment_label === 'Negative').length;

  return (
    <main className="container">
      <header>
        <h1 className="title">Financial Intelligence</h1>
        <p className="subtitle">Real-time market sentiment derived from global news sources.</p>
      </header>

      <section className="stats-grid">
        <div className="stat-card">
          <div className="stat-value">{total}</div>
          <div className="stat-label">Articles Analyzed</div>
        </div>
        <div className="stat-card" style={{ borderColor: 'rgba(16, 185, 129, 0.3)' }}>
          <div className="stat-value" style={{ color: '#10b981' }}>
            {total > 0 ? Math.round((positiveCount / total) * 100) : 0}%
          </div>
          <div className="stat-label">Positive Sentiment</div>
        </div>
        <div className="stat-card" style={{ borderColor: 'rgba(239, 68, 68, 0.3)' }}>
          <div className="stat-value" style={{ color: '#ef4444' }}>
            {total > 0 ? Math.round((negativeCount / total) * 100) : 0}%
          </div>
          <div className="stat-label">Negative Sentiment</div>
        </div>
      </section>

      <section className="news-grid">
        {articles.map((article) => (
          <a href={article.url} target="_blank" rel="noopener noreferrer" className="news-card" key={article.id}>
            <div className="card-header">
              <span className="source">{article.source_name || "News"}</span>
              <span className="date">
                {new Date(article.publishedAt).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
              </span>
            </div>
            
            <h2 className="card-title">{article.title}</h2>
            
            <div className="card-footer">
              <div className={`badge ${article.sentiment_label.toLowerCase()}`}>
                {article.sentiment_label === 'Positive' && '▲'}
                {article.sentiment_label === 'Negative' && '▼'}
                {article.sentiment_label === 'Neutral' && '■'}
                {article.sentiment_label}
              </div>
              <div className="score">
                Score: {article.sentiment_score.toFixed(2)}
              </div>
            </div>
          </a>
        ))}
      </section>
    </main>
  );
}
