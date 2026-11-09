"""SQLAlchemy models for the research data layer."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Ticker(Base):
    __tablename__ = "tickers"

    symbol: Mapped[str] = mapped_column(String(10), primary_key=True)
    company_name: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (Index("ix_articles_published_at", "published_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    url: Mapped[str] = mapped_column(Text, unique=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArticleTicker(Base):
    __tablename__ = "article_tickers"
    # The PK covers lookups by article_id; this index covers "articles for a symbol".
    __table_args__ = (Index("ix_article_tickers_symbol", "symbol", "article_id"),)

    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    symbol: Mapped[str] = mapped_column(ForeignKey("tickers.symbol"), primary_key=True)


class Sentiment(Base):
    __tablename__ = "sentiment"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    model: Mapped[str] = mapped_column(Text, primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    label: Mapped[str] = mapped_column(Text)


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    # The (symbol, date) primary key doubles as the symbol + date index.

    symbol: Mapped[str] = mapped_column(ForeignKey("tickers.symbol"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(BigInteger)


class FetchLog(Base):
    """One NewsAPI request covering one ticker and one New York calendar day."""

    __tablename__ = "fetch_log"

    symbol: Mapped[str] = mapped_column(ForeignKey("tickers.symbol"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    articles_returned: Mapped[int] = mapped_column(Integer)
    total_results: Mapped[int] = mapped_column(Integer)
    capped: Mapped[bool] = mapped_column(Boolean)
    settled: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(
        Text,
        Computed(
            "CASE WHEN NOT settled THEN 'provisional' WHEN capped THEN 'capped' "
            "ELSE 'complete' END",
            persisted=True,
        ),
    )
