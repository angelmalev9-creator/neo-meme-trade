import { createClient } from '@supabase/supabase-js';

const supabaseUrl = (import.meta as any).env.VITE_SUPABASE_URL || '';
const supabaseAnonKey = (import.meta as any).env.VITE_SUPABASE_ANON_KEY || '';

// We export a helper to verify if real Supabase config is active
export const isSupabaseConfigured = !!(supabaseUrl && supabaseAnonKey);

// Real client or mock fallback depending on configuration
export const supabase = isSupabaseConfigured
  ? createClient(supabaseUrl, supabaseAnonKey)
  : null;

// Mock database service to emulate Supabase logic in sandbox environment
class MockDatabaseService {
  private getStorageItem<T>(key: string, defaultValue: T): T {
    try {
      const saved = localStorage.getItem(key);
      return saved ? JSON.parse(saved) : defaultValue;
    } catch (e) {
      return defaultValue;
    }
  }

  private setStorageItem<T>(key: string, value: T): void {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch (e) {}
  }

  // Users Auth & Plan table mock
  async getUser(username: string) {
    const users = this.getStorageItem<any>('saas_registered_users', {});
    const user = users[username.toLowerCase().trim()];
    if (!user) return { data: null, error: new Error('User not found') };
    return { data: user, error: null };
  }

  async saveUser(user: { username: string; walletAddress?: string; plan: string; activeUntil?: string }) {
    const users = this.getStorageItem<any>('saas_registered_users', {});
    const key = user.username.toLowerCase().trim();
    const updatedUser = {
      ...users[key],
      ...user,
      updatedAt: new Date().toISOString()
    };
    users[key] = updatedUser;
    this.setStorageItem('saas_registered_users', users);
    return { data: updatedUser, error: null };
  }

  // Deposits & Ledger mock
  async getDeposits(username: string) {
    const key = `saas_deposits_${username.toLowerCase()}`;
    const deposits = this.getStorageItem<any[]>(key, []);
    return { data: deposits, error: null };
  }

  async addDeposit(username: string, amountSol: number, txSignature: string, status: 'PENDING' | 'CONFIRMED') {
    const key = `saas_deposits_${username.toLowerCase()}`;
    const deposits = this.getStorageItem<any[]>(key, []);
    const newDeposit = {
      id: Math.random().toString(36).substring(7),
      username,
      amountSol,
      txSignature,
      status,
      createdAt: new Date().toISOString()
    };
    deposits.unshift(newDeposit);
    this.setStorageItem(key, deposits);
    return { data: newDeposit, error: null };
  }

  // Trades Ledger mock
  async getTrades(username: string) {
    const key = `saas_trades_${username.toLowerCase()}`;
    const trades = this.getStorageItem<any[]>(key, []);
    return { data: trades, error: null };
  }

  async addTrade(username: string, trade: { tokenAddress: string; tokenSymbol: string; type: 'BUY' | 'SELL'; amountSol: number; profitSol?: number }) {
    const key = `saas_trades_${username.toLowerCase()}`;
    const trades = this.getStorageItem<any[]>(key, []);
    const newTrade = {
      id: Math.random().toString(36).substring(7),
      ...trade,
      createdAt: new Date().toISOString()
    };
    trades.unshift(newTrade);
    this.setStorageItem(key, trades);
    return { data: newTrade, error: null };
  }
}

export const dbService = new MockDatabaseService();

/**
 * SQL SCHEMA FOR SUPABASE SETUP
 * Copy and execute the following SQL in Supabase SQL Editor:
 * 
 * -- 1. Users Profile Table
 * CREATE TABLE public.users (
 *   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 *   username TEXT UNIQUE NOT NULL,
 *   wallet_address TEXT,
 *   plan TEXT DEFAULT 'EX-1000 DEMO',
 *   active_until TIMESTAMP WITH TIME ZONE,
 *   created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
 * );
 * 
 * -- 2. Deposits Ledger Table (Manual verification via Revolut/Crypto or real on-chain checks)
 * CREATE TABLE public.deposits (
 *   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 *   username TEXT NOT NULL REFERENCES public.users(username) ON DELETE CASCADE,
 *   amount_sol NUMERIC NOT NULL,
 *   tx_signature TEXT,
 *   status TEXT DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'CONFIRMED', 'FAILED')),
 *   created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
 * );
 * 
 * -- 3. Sniper Trades Ledger Table (To track the live trading loops and performance history)
 * CREATE TABLE public.trades (
 *   id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 *   username TEXT NOT NULL REFERENCES public.users(username) ON DELETE CASCADE,
 *   token_address TEXT NOT NULL,
 *   token_symbol TEXT NOT NULL,
 *   trade_type TEXT CHECK (trade_type IN ('BUY', 'SELL')),
 *   amount_sol NUMERIC NOT NULL,
 *   profit_sol NUMERIC DEFAULT 0,
 *   created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
 * );
 */
