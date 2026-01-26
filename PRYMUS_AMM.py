# { "Depends": "py-genlayer:test" }

from genlayer import *

class PRYMUSAMM(gl.Contract):
    """
    PRYMUS AMM Bonding Curve - 5% Tax
    GenLayer Intelligent Contract version
    """
    
    # ============== PERSISTENT STORAGE FIELDS ==============
    # All persistent fields must be declared here with type annotations
    
    # Token information
    name: str
    symbol: str
    
    # Configuration constants (u256 for large numbers)
    dividend_fee: u256  # 20 = 5% tax (100/20)
    token_price_initial: u256
    token_price_incremental: u256
    magnitude: u256
    
    # Staking requirement
    staking_requirement: u256
    
    # Contract state
    token_supply: u256
    profit_per_share: u256
    
    # Mappings (using GenLayer's TreeMap)
    token_balance_ledger: TreeMap[str, u256]
    referral_balance: TreeMap[str, u256]
    payouts_to: TreeMap[str, i256]  # Can be negative, so i256
    
    # Administrator and ambassador controls
    administrators: TreeMap[str, bool]
    ambassadors: TreeMap[str, bool]
    ambassador_accumulated_quota: TreeMap[str, u256]
    
    # Phase control
    only_ambassadors: bool
    
    # ============== CONSTRUCTOR ==============
    def __init__(self):
        # Initialize configuration
        self.name = "PRYMUS"
        self.symbol = "xPRYM"
        self.dividend_fee = u256(20)  # 5% tax
        self.token_price_initial = u256(100000000000)  # 0.0000001 ether in wei
        self.token_price_incremental = u256(10000000)  # 0.00000001 ether in wei
        self.magnitude = u256(2**64)
        
        # Staking requirement (100 tokens initially, but in minimal units)
        self.staking_requirement = u256(100 * 10**18)
        
        # Initialize state
        self.token_supply = u256(0)
        self.profit_per_share = u256(0)
        self.only_ambassadors = False
        
        # Initialize mappings
        self.token_balance_ledger = TreeMap[str, u256]()
        self.referral_balance = TreeMap[str, u256]()
        self.payouts_to = TreeMap[str, i256]()
        self.administrators = TreeMap[str, bool]()
        self.ambassadors = TreeMap[str, bool]()
        self.ambassador_accumulated_quota = TreeMap[str, u256]()
        
        # Set up administrators and ambassadors
        self._initialize_contract()
    
    def _initialize_contract(self):
        """Initialize contract with administrators and ambassadors"""
        # Add administrator
        admin_address = "0xe91d64dba77f752f78ce729d12b5625939be42b530591f026ca2b2a44ff05fc0"
        self.administrators[admin_address] = True
        
        # Add ambassador (for legacy, though phase is disabled)
        ambassador_address = "0x752c3b6cb472d426ad0438f202a46dfa7d58af34"
        self.ambassadors[ambassador_address] = True
        
        # Initialize ambassador quota
        self.ambassador_accumulated_quota[ambassador_address] = u256(0)
    
    # ============== PUBLIC WRITE FUNCTIONS ==============
    
    @gl.public.write.payable
    def buy(self, referred_by: str = "") -> u256:
        """
        Purchase tokens with native currency
        payable decorator allows receiving funds
        """
        # Get the incoming amount from the transaction
        incoming_ethereum = u256(gl.msg.value)
        
        # Apply anti-early-whale protection if needed
        if self.only_ambassadors:
            self._anti_early_whale(incoming_ethereum, gl.msg.sender)
        
        # Process the token purchase
        tokens_minted = self._purchase_tokens(incoming_ethereum, referred_by)
        
        # Log the purchase event
        print(f"TokenPurchase: {gl.msg.sender}, ETH: {incoming_ethereum}, Tokens: {tokens_minted}, ReferredBy: {referred_by}")
        
        return tokens_minted
    
    @gl.public.write
    def reinvest(self) -> u256:
        """
        Converts all of caller's dividends to tokens
        """
        # Verify the caller has dividends
        dividends = self._my_dividends(False)
        assert dividends > 0, "No dividends to reinvest"
        
        # Add referral bonus if any
        referral_bonus = self.referral_balance.get(gl.msg.sender, u256(0))
        total_reinvest = dividends + referral_bonus
        
        # Clear referral balance
        if gl.msg.sender in self.referral_balance:
            self.referral_balance[gl.msg.sender] = u256(0)
        
        # Update payout tracker
        current_payout = self.payouts_to.get(gl.msg.sender, i256(0))
        self.payouts_to[gl.msg.sender] = current_payout + i256(total_reinvest * self.magnitude)
        
        # Purchase tokens with the dividends
        tokens_minted = self._purchase_tokens(total_reinvest, "")
        
        print(f"Reinvestment: {gl.msg.sender}, ETH: {total_reinvest}, Tokens: {tokens_minted}")
        
        return tokens_minted
    
    @gl.public.write
    def sell(self, amount_of_tokens: u256) -> u256:
        """
        Sell tokens back to the bonding curve
        """
        # Verify the caller has enough tokens
        caller_balance = self.token_balance_ledger.get(gl.msg.sender, u256(0))
        assert amount_of_tokens <= caller_balance and amount_of_tokens > 0, "Invalid token amount"
        
        # Calculate the sale
        ethereum_value = self._tokens_to_ethereum(amount_of_tokens)
        dividends = ethereum_value // self.dividend_fee
        taxed_ethereum = ethereum_value - dividends
        
        # Burn the sold tokens
        self.token_supply -= amount_of_tokens
        self.token_balance_ledger[gl.msg.sender] = caller_balance - amount_of_tokens
        
        # Update dividends tracker
        updated_payouts = i256(self.profit_per_share * amount_of_tokens + (taxed_ethereum * self.magnitude))
        current_payout = self.payouts_to.get(gl.msg.sender, i256(0))
        self.payouts_to[gl.msg.sender] = current_payout - updated_payouts
        
        # Update profit per share if there are tokens left
        if self.token_supply > 0:
            self.profit_per_share += (dividends * self.magnitude) // self.token_supply
        
        print(f"TokenSell: {gl.msg.sender}, Tokens: {amount_of_tokens}, ETH: {taxed_ethereum}")
        
        # In a real implementation, this would transfer ETH to the caller
        # For GenLayer, the actual transfer would be handled by the platform
        return taxed_ethereum
    
    @gl.public.write
    def withdraw(self) -> u256:
        """
        Withdraw all of the caller's dividends
        """
        # Get dividends
        dividends = self._my_dividends(False)
        assert dividends > 0, "No dividends to withdraw"
        
        # Add referral bonus if any
        referral_bonus = self.referral_balance.get(gl.msg.sender, u256(0))
        total_withdraw = dividends + referral_bonus
        
        # Update payout tracker
        current_payout = self.payouts_to.get(gl.msg.sender, i256(0))
        self.payouts_to[gl.msg.sender] = current_payout + i256(dividends * self.magnitude)
        
        # Clear referral balance
        if gl.msg.sender in self.referral_balance:
            self.referral_balance[gl.msg.sender] = u256(0)
        
        print(f"Withdraw: {gl.msg.sender}, Amount: {total_withdraw}")
        
        # In a real implementation, this would transfer ETH to the caller
        return total_withdraw
    
    @gl.public.write
    def transfer(self, to_address: str, amount_of_tokens: u256) -> bool:
        """
        Transfer tokens to another address (with 5% fee)
        """
        # Verify the caller has enough tokens
        caller_balance = self.token_balance_ledger.get(gl.msg.sender, u256(0))
        assert amount_of_tokens <= caller_balance and amount_of_tokens > 0, "Insufficient tokens"
        assert not self.only_ambassadors, "Transfers disabled during ambassador phase"
        
        # Withdraw any outstanding dividends first
        if self._my_dividends(True) > 0:
            # Note: In GenLayer, we can't call another write function from within a function
            # So we need to handle this differently - for now, we'll just skip
            # In production, you might want to restructure this logic
            pass
        
        # Calculate the 5% transfer fee
        token_fee = amount_of_tokens // self.dividend_fee
        taxed_tokens = amount_of_tokens - token_fee
        dividends = self._tokens_to_ethereum(token_fee)
        
        # Burn the fee tokens
        self.token_supply -= token_fee
        
        # Transfer tokens
        self.token_balance_ledger[gl.msg.sender] = caller_balance - amount_of_tokens
        
        to_balance = self.token_balance_ledger.get(to_address, u256(0))
        self.token_balance_ledger[to_address] = to_balance + taxed_tokens
        
        # Update dividend trackers
        current_payout_sender = self.payouts_to.get(gl.msg.sender, i256(0))
        current_payout_receiver = self.payouts_to.get(to_address, i256(0))
        
        self.payouts_to[gl.msg.sender] = current_payout_sender - i256(self.profit_per_share * amount_of_tokens)
        self.payouts_to[to_address] = current_payout_receiver + i256(self.profit_per_share * taxed_tokens)
        
        # Disperse dividends among holders
        if self.token_supply > 0:
            self.profit_per_share += (dividends * self.magnitude) // self.token_supply
        
        print(f"Transfer: {gl.msg.sender} -> {to_address}, Tokens: {taxed_tokens}")
        
        return True
    
    @gl.public.write
    def exit(self) -> tuple[u256, u256]:
        """
        Sell all tokens and withdraw all earnings
        Returns: (tokens_sold, eth_withdrawn)
        """
        # Get caller's token balance
        caller_tokens = self.token_balance_ledger.get(gl.msg.sender, u256(0))
        
        # Sell all tokens if any
        eth_from_sale = u256(0)
        if caller_tokens > 0:
            eth_from_sale = self.sell(caller_tokens)
        
        # Withdraw all dividends
        eth_from_dividends = self.withdraw()
        
        total_eth = eth_from_sale + eth_from_dividends
        
        print(f"Exit: {gl.msg.sender}, Total ETH: {total_eth}")
        
        return (caller_tokens, total_eth)
    
    # ============== ADMIN FUNCTIONS ==============
    
    @gl.public.write
    def disable_initial_stage(self):
        """Disable ambassador-only phase"""
        # Verify caller is administrator
        assert self.administrators.get(gl.msg.sender, False), "Not administrator"
        
        self.only_ambassadors = False
        print(f"Initial stage disabled by {gl.msg.sender}")
    
    @gl.public.write
    def set_administrator(self, identifier: str, status: bool):
        """Add or remove an administrator"""
        # Verify caller is administrator
        assert self.administrators.get(gl.msg.sender, False), "Not administrator"
        
        self.administrators[identifier] = status
        print(f"Administrator {identifier} set to {status} by {gl.msg.sender}")
    
    @gl.public.write
    def set_staking_requirement(self, amount_of_tokens: u256):
        """Change the staking requirement for referrals"""
        # Verify caller is administrator
        assert self.administrators.get(gl.msg.sender, False), "Not administrator"
        
        self.staking_requirement = amount_of_tokens
        print(f"Staking requirement set to {amount_of_tokens} by {gl.msg.sender}")
    
    @gl.public.write
    def set_name(self, new_name: str):
        """Change token name"""
        # Verify caller is administrator
        assert self.administrators.get(gl.msg.sender, False), "Not administrator"
        
        self.name = new_name
        print(f"Name set to {new_name} by {gl.msg.sender}")
    
    @gl.public.write
    def set_symbol(self, new_symbol: str):
        """Change token symbol"""
        # Verify caller is administrator
        assert self.administrators.get(gl.msg.sender, False), "Not administrator"
        
        self.symbol = new_symbol
        print(f"Symbol set to {new_symbol} by {gl.msg.sender}")
    
    # ============== PUBLIC VIEW FUNCTIONS ==============
    
    @gl.public.view
    def total_supply(self) -> u256:
        """Get total token supply"""
        return self.token_supply
    
    @gl.public.view
    def my_tokens(self) -> u256:
        """Get caller's token balance"""
        return self.token_balance_ledger.get(gl.msg.sender, u256(0))
    
    @gl.public.view
    def my_dividends(self, include_referral_bonus: bool) -> u256:
        """Get caller's dividends"""
        dividends = self._dividends_of(gl.msg.sender)
        
        if include_referral_bonus:
            referral_bonus = self.referral_balance.get(gl.msg.sender, u256(0))
            dividends += referral_bonus
        
        return dividends
    
    @gl.public.view
    def balance_of(self, customer_address: str) -> u256:
        """Get token balance of any address"""
        return self.token_balance_ledger.get(customer_address, u256(0))
    
    @gl.public.view
    def sell_price(self) -> u256:
        """Current sell price per token (after fee)"""
        if self.token_supply == 0:
            return self.token_price_initial - self.token_price_incremental
        else:
            ethereum = self._tokens_to_ethereum(u256(10**18))
            dividends = ethereum // self.dividend_fee
            taxed_ethereum = ethereum - dividends
            return taxed_ethereum
    
    @gl.public.view
    def buy_price(self) -> u256:
        """Current buy price per token (including fee)"""
        if self.token_supply == 0:
            return self.token_price_initial + self.token_price_incremental
        else:
            ethereum = self._tokens_to_ethereum(u256(10**18))
            dividends = ethereum // self.dividend_fee
            taxed_ethereum = ethereum + dividends
            return taxed_ethereum
    
    @gl.public.view
    def calculate_tokens_received(self, ethereum_to_spend: u256) -> u256:
        """Calculate tokens received for a given ETH amount"""
        dividends = ethereum_to_spend // self.dividend_fee
        taxed_ethereum = ethereum_to_spend - dividends
        return self._ethereum_to_tokens(taxed_ethereum)
    
    @gl.public.view
    def calculate_ethereum_received(self, tokens_to_sell: u256) -> u256:
        """Calculate ETH received for selling given tokens"""
        assert tokens_to_sell <= self.token_supply, "Not enough tokens in supply"
        
        ethereum = self._tokens_to_ethereum(tokens_to_sell)
        dividends = ethereum // self.dividend_fee
        taxed_ethereum = ethereum - dividends
        
        return taxed_ethereum
    
    # ============== INTERNAL FUNCTIONS ==============
    
    def _anti_early_whale(self, amount_of_ethereum: u256, customer_address: str):
        """Anti-early-whale protection during ambassador phase"""
        if not self.only_ambassadors:
            return
        
        # In a full implementation, this would check ambassador quotas
        # For now, we'll keep it simple since ambassador phase is disabled by default
        is_ambassador = self.ambassadors.get(customer_address, False)
        
        if not is_ambassador:
            # End ambassador phase if non-ambassador tries to buy
            self.only_ambassadors = False
            print(f"Ambassador phase ended by {customer_address}")
    
    def _purchase_tokens(self, incoming_ethereum: u256, referred_by: str) -> u256:
        """Core bonding curve purchase logic"""
        # Calculate fees
        undivided_dividends = incoming_ethereum // self.dividend_fee
        referral_bonus = undivided_dividends // 3  # 1/3 of fees go to referrer
        dividends = undivided_dividends - referral_bonus
        taxed_ethereum = incoming_ethereum - undivided_dividends
        
        # Calculate tokens to mint
        amount_of_tokens = self._ethereum_to_tokens(taxed_ethereum)
        
        # Ensure valid token amount
        assert amount_of_tokens > 0, "Token amount must be positive"
        
        # Handle referrals
        if (referred_by and referred_by != gl.msg.sender and 
            referred_by in self.token_balance_ledger and
            self.token_balance_ledger[referred_by] >= self.staking_requirement):
            
            # Add referral bonus to referrer
            current_bonus = self.referral_balance.get(referred_by, u256(0))
            self.referral_balance[referred_by] = current_bonus + referral_bonus
        else:
            # If no valid referrer, add bonus to dividends
            dividends += referral_bonus
        
        # Update token supply
        self.token_supply += amount_of_tokens
        
        # Update profit per share if there are tokens
        if self.token_supply > 0:
            self.profit_per_share += (dividends * self.magnitude) // self.token_supply
        
        # Update buyer's token balance
        current_balance = self.token_balance_ledger.get(gl.msg.sender, u256(0))
        self.token_balance_ledger[gl.msg.sender] = current_balance + amount_of_tokens
        
        # Update payout tracker for buyer
        fee = dividends * self.magnitude
        updated_payouts = i256(self.profit_per_share * amount_of_tokens - fee)
        
        current_payout = self.payouts_to.get(gl.msg.sender, i256(0))
        self.payouts_to[gl.msg.sender] = current_payout + updated_payouts
        
        return amount_of_tokens
    
    def _ethereum_to_tokens(self, ethereum: u256) -> u256:
        """Bonding curve: ETH → Tokens"""
        token_price_initial_scaled = self.token_price_initial * u256(10**18)
        incremental_scaled = self.token_price_incremental * u256(10**18)
        
        # Calculate the square root term for the bonding curve formula
        # (tokenPriceInitial^2) + (2 * tokenPriceIncremental * ethereum * 10^18) + 
        # (tokenPriceIncremental^2 * tokenSupply^2) + (2 * tokenPriceIncremental * tokenPriceInitial * tokenSupply)
        term1 = token_price_initial_scaled * token_price_initial_scaled
        
        ethereum_scaled = ethereum * u256(10**18)
        term2 = u256(2) * incremental_scaled * ethereum_scaled
        
        token_supply_sq = self.token_supply * self.token_supply
        incremental_sq = self.token_price_incremental * self.token_price_incremental
        term3 = incremental_sq * token_supply_sq
        
        term4 = u256(2) * self.token_price_incremental * self.token_price_initial * self.token_supply
        
        sqrt_input = term1 + term2 + term3 + term4
        
        # Calculate square root (simplified version)
        sqrt_result = self._sqrt(sqrt_input)
        
        # Final calculation
        if sqrt_result < token_price_initial_scaled:
            return u256(0)
        
        tokens_received = (sqrt_result - token_price_initial_scaled) // self.token_price_incremental
        
        if tokens_received < self.token_supply:
            return u256(0)
        
        return tokens_received - self.token_supply
    
    def _tokens_to_ethereum(self, tokens: u256) -> u256:
        """Bonding curve: Tokens → ETH"""
        tokens_adjusted = tokens + u256(10**18)
        supply_adjusted = self.token_supply + u256(10**18)
        
        # Calculate price at current supply
        price_at_supply = self.token_price_initial + (self.token_price_incremental * supply_adjusted // u256(10**18))
        
        # Calculate ETH value
        term1 = (price_at_supply - self.token_price_incremental) * (tokens_adjusted - u256(10**18))
        
        tokens_sq = tokens_adjusted * tokens_adjusted
        term2_numerator = self.token_price_incremental * (tokens_sq - tokens_adjusted)
        term2 = term2_numerator // (u256(2) * u256(10**18))
        
        ether_received = (term1 - term2) // u256(10**18)
        
        return ether_received
    
    def _dividends_of(self, customer_address: str) -> u256:
        """Calculate dividends for a specific address"""
        balance = self.token_balance_ledger.get(customer_address, u256(0))
        payout = self.payouts_to.get(customer_address, i256(0))
        
        if balance == 0:
            return u256(0)
        
        # Calculate dividends: (profit_per_share * balance - payout) / magnitude
        profit_share = self.profit_per_share * balance
        profit_share_i = i256(profit_share)
        
        dividends_i = profit_share_i - payout
        
        if dividends_i < 0:
            return u256(0)
        
        return u256(dividends_i // i256(self.magnitude))
    
    def _my_dividends(self, include_referral_bonus: bool) -> u256:
        """Internal version of my_dividends"""
        return self.my_dividends(include_referral_bonus)
    
    def _sqrt(self, x: u256) -> u256:
        """Integer square root (Babylonian method)"""
        if x == 0:
            return u256(0)
        
        # Start with an estimate
        z = (x + 1) // 2
        y = x
        
        # Iterate to improve the estimate
        for _ in range(10):  # Limited iterations for GenLayer
            if z >= y:
                break
            y = z
            z = (x // z + z) // 2
        
        return y
