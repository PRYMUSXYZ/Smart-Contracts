pragma solidity ^0.4.20;

// PRYMUS AMM Bonding Curve - 5% Tax
// Built on Asimov Testnet / Ready for Bradbury Testnet

contract Hourglass {
    /*=================================
    =            MODIFIERS            =
    =================================*/
    // only people with tokens
    modifier onlyTokenHolders() {
        require(myTokens() > 0);
        _;
    }

    // only people with profits
    modifier onlyProfitEligible() {
        require(myDividends(true) > 0);
        _;
    }

    modifier onlyAdministrator() {
        address _adminAddress = msg.sender;
        require(administrators[keccak256(_adminAddress)]);
        _;
    }

    // ensures that the first tokens in the contract will be equally distributed
    // meaning, no large initial dump will be ever possible
    // result: healthy longevity.
    modifier antiEarlyWhale(uint256 _investmentAmount) {
        address _investorAddress = msg.sender;

        // are we still in the vulnerable phase?
        // if so, enact anti early whale protocol
        if (earlyAccessPhase && ((totalContractBalance() - _investmentAmount) <= earlyAccessQuota)) {
            require(
                // is the investor in the early access list?
                earlyAccessMembers[_investorAddress] == true &&

                // does the investment exceed the max early access quota?
                (earlyAccessAccumulatedQuota[_investorAddress] + _investmentAmount) <= earlyAccessMaxInvestment
            );

            // update the accumulated quota
            earlyAccessAccumulatedQuota[_investorAddress] = SafeMath.add(
                earlyAccessAccumulatedQuota[_investorAddress], 
                _investmentAmount
            );

            // execute
            _;
        } else {
            // in case the ether count drops low, the early access phase won't reinitiate
            earlyAccessPhase = false;
            _;
        }
    }

    /*==============================
    =            EVENTS            =
    ==============================*/
    event TokensPurchased(
        address indexed investorAddress,
        uint256 investmentAmount,
        uint256 tokensMinted,
        address indexed referredBy
    );

    event TokensSold(
        address indexed investorAddress,
        uint256 tokensBurned,
        uint256 proceedsAmount
    );

    event Reinvestment(
        address indexed investorAddress,
        uint256 amountReinvested,
        uint256 tokensMinted
    );

    event WithdrawalExecuted(
        address indexed investorAddress,
        uint256 withdrawalAmount
    );

    // ERC20 standard event
    event Transfer(
        address indexed from,
        address indexed to,
        uint256 tokens
    );

    /*=====================================
    =            CONFIGURABLES            =
    =====================================*/
    string public name = "PRYMUS";
    string public symbol = "PRYM";
    uint8 constant public decimals = 18;
    uint8 constant internal dividendFeePercent = 20; // 5% effective fee (100/20 = 5%)
    uint256 constant internal initialTokenPrice = 0.000000001 ether;
    uint256 constant internal tokenPriceIncrement = 0.0000000001 ether;
    uint256 constant internal magnitude = 2**64;

    // proof of stake (defaults at 100 tokens)
    uint256 public minimumHoldingRequirement = 10e18;

    // early access program
    mapping(address => bool) internal earlyAccessMembers;
    uint256 constant internal earlyAccessMaxInvestment = 1 ether;
    uint256 constant internal earlyAccessQuota = 1 ether;

    /*================================
    =            DATASETS            =
    ================================*/
    // amount of shares for each address (scaled number)
    mapping(address => uint256) internal tokenBalanceLedger;
    mapping(address => uint256) internal referralBalance;
    mapping(address => int256) internal payouts;
    mapping(address => uint256) internal earlyAccessAccumulatedQuota;
    uint256 internal totalTokenSupply = 0;
    uint256 internal profitPerShare;

    // administrator list
    mapping(bytes32 => bool) public administrators;

    // when this is set to true, only early access members can purchase tokens
    bool public earlyAccessPhase = false;

    /*=======================================
    =            PUBLIC FUNCTIONS            =
    =======================================*/
    /*
    * -- APPLICATION ENTRY POINTS --
    */
    function Hourglass() public {
        // add administrators here
        administrators[0xe91d64dba77f752f78ce729d12b5625939be42b530591f026ca2b2a44ff05fc0] = true;

        // add early access members
        earlyAccessMembers[0x752c3b6cb472d426ad0438f202a46dfa7d58af34] = true;
    }

    /**
     * Converts incoming Native Currency to tokens
     */
    function purchaseTokens(address _referredBy) public payable returns(uint256) {
        return _processTokenPurchase(msg.value, _referredBy);
    }

    /**
     * Fallback function to handle direct sends
     */
    function() payable public {
        _processTokenPurchase(msg.value, address(0));
    }

    /**
     * Converts all of caller's dividends to tokens
     */
    function reinvest() onlyProfitEligible() public {
        // fetch dividends
        uint256 _dividends = myDividends(false);

        // pay out the dividends virtually
        address _investorAddress = msg.sender;
        payouts[_investorAddress] += (int256)(_dividends * magnitude);

        // retrieve referral bonus
        _dividends += referralBalance[_investorAddress];
        referralBalance[_investorAddress] = 0;

        // dispatch a purchase order with the virtualized dividends
        uint256 _tokens = _processTokenPurchase(_dividends, address(0));

        // fire event
        Reinvestment(_investorAddress, _dividends, _tokens);
    }

    /**
     * Alias of sell() and withdraw()
     */
    function liquidatePosition() public {
        address _investorAddress = msg.sender;
        uint256 _tokenAmount = tokenBalanceLedger[_investorAddress];
        
        if (_tokenAmount > 0) {
            liquidateTokens(_tokenAmount);
        }
        
        withdrawYield();
    }

    /**
     * Withdraws all of the caller's earnings
     */
    function withdrawYield() onlyProfitEligible() public {
        address _investorAddress = msg.sender;
        uint256 _dividends = myDividends(false);

        // update dividend tracker
        payouts[_investorAddress] += (int256)(_dividends * magnitude);

        // add referral bonus
        _dividends += referralBalance[_investorAddress];
        referralBalance[_investorAddress] = 0;

        // execute transfer
        _investorAddress.transfer(_dividends);

        // fire event
        WithdrawalExecuted(_investorAddress, _dividends);
    }

    /**
     * Converts tokens to native currency
     */
    function liquidateTokens(uint256 _tokenAmount) onlyTokenHolders() public {
        address _investorAddress = msg.sender;
        
        require(_tokenAmount <= tokenBalanceLedger[_investorAddress]);
        
        uint256 _ethereumValue = _tokensToEthereum(_tokenAmount);
        uint256 _dividends = SafeMath.div(_ethereumValue, dividendFeePercent);
        uint256 _taxedEthereum = SafeMath.sub(_ethereumValue, _dividends);

        // burn the sold tokens
        totalTokenSupply = SafeMath.sub(totalTokenSupply, _tokenAmount);
        tokenBalanceLedger[_investorAddress] = SafeMath.sub(
            tokenBalanceLedger[_investorAddress], 
            _tokenAmount
        );

        // update dividends tracker
        int256 _updatedPayouts = (int256)(profitPerShare * _tokenAmount + (_taxedEthereum * magnitude));
        payouts[_investorAddress] -= _updatedPayouts;

        // update profit per share
        if (totalTokenSupply > 0) {
            profitPerShare = SafeMath.add(
                profitPerShare, 
                (_dividends * magnitude) / totalTokenSupply
            );
        }

        // fire event
        TokensSold(_investorAddress, _tokenAmount, _taxedEthereum);
    }

    /**
     * Transfer tokens to another address
     */
    function transfer(address _recipient, uint256 _tokenAmount) 
        onlyTokenHolders() 
        public 
        returns(bool) 
    {
        address _sender = msg.sender;

        require(!earlyAccessPhase && _tokenAmount <= tokenBalanceLedger[_sender]);

        // withdraw all outstanding dividends first
        if (myDividends(true) > 0) {
            withdrawYield();
        }

        // calculate transfer fee
        uint256 _transferFee = SafeMath.div(_tokenAmount, dividendFeePercent);
        uint256 _taxedTokens = SafeMath.sub(_tokenAmount, _transferFee);
        uint256 _dividends = _tokensToEthereum(_transferFee);

        // burn the fee tokens
        totalTokenSupply = SafeMath.sub(totalTokenSupply, _transferFee);

        // execute transfer
        tokenBalanceLedger[_sender] = SafeMath.sub(tokenBalanceLedger[_sender], _tokenAmount);
        tokenBalanceLedger[_recipient] = SafeMath.add(tokenBalanceLedger[_recipient], _taxedTokens);

        // update dividend trackers
        payouts[_sender] -= (int256)(profitPerShare * _tokenAmount);
        payouts[_recipient] += (int256)(profitPerShare * _taxedTokens);

        // distribute dividends among holders
        profitPerShare = SafeMath.add(
            profitPerShare, 
            (_dividends * magnitude) / totalTokenSupply
        );

        // fire event
        Transfer(_sender, _recipient, _taxedTokens);

        return true;
    }

    /*----------  ADMINISTRATOR FUNCTIONS  ----------*/
    function disableEarlyAccessPhase() onlyAdministrator() public {
        earlyAccessPhase = false;
    }

    function setAdministrator(bytes32 _identifier, bool _status) onlyAdministrator() public {
        administrators[_identifier] = _status;
    }

    function setMinimumHolding(uint256 _tokenAmount) onlyAdministrator() public {
        minimumHoldingRequirement = _tokenAmount;
    }

    function setName(string _name) onlyAdministrator() public {
        name = _name;
    }

    function setSymbol(string _symbol) onlyAdministrator() public {
        symbol = _symbol;
    }

    /*----------  VIEW FUNCTIONS  ----------*/
    function totalContractBalance() public view returns(uint) {
        return address(this).balance;
    }

    function totalSupply() public view returns(uint256) {
        return totalTokenSupply;
    }

    function myTokens() public view returns(uint256) {
        address _investorAddress = msg.sender;
        return tokenBalanceOf(_investorAddress);
    }

    function myDividends(bool _includeReferralBonus) public view returns(uint256) {
        address _investorAddress = msg.sender;
        return _includeReferralBonus ? 
            dividendsOf(_investorAddress) + referralBalance[_investorAddress] : 
            dividendsOf(_investorAddress);
    }

    function tokenBalanceOf(address _investorAddress) public view returns(uint256) {
        return tokenBalanceLedger[_investorAddress];
    }

    function dividendsOf(address _investorAddress) public view returns(uint256) {
        return (uint256)(
            (int256)(profitPerShare * tokenBalanceLedger[_investorAddress]) - 
            payouts[_investorAddress]
        ) / magnitude;
    }

    function currentSellPrice() public view returns(uint256) {
        if (totalTokenSupply == 0) {
            return initialTokenPrice - tokenPriceIncrement;
        } else {
            uint256 _ethereum = _tokensToEthereum(1e18);
            uint256 _dividends = SafeMath.div(_ethereum, dividendFeePercent);
            uint256 _taxedEthereum = SafeMath.sub(_ethereum, _dividends);
            return _taxedEthereum;
        }
    }

    function currentBuyPrice() public view returns(uint256) {
        if (totalTokenSupply == 0) {
            return initialTokenPrice + tokenPriceIncrement;
        } else {
            uint256 _ethereum = _tokensToEthereum(1e18);
            uint256 _dividends = SafeMath.div(_ethereum, dividendFeePercent);
            uint256 _taxedEthereum = SafeMath.add(_ethereum, _dividends);
            return _taxedEthereum;
        }
    }

    function calculateTokensReceived(uint256 _investmentAmount) public view returns(uint256) {
        uint256 _dividends = SafeMath.div(_investmentAmount, dividendFeePercent);
        uint256 _taxedEthereum = SafeMath.sub(_investmentAmount, _dividends);
        uint256 _tokens = _ethereumToTokens(_taxedEthereum);
        return _tokens;
    }

    function calculateEthereumReceived(uint256 _tokenAmount) public view returns(uint256) {
        require(_tokenAmount <= totalTokenSupply);
        uint256 _ethereum = _tokensToEthereum(_tokenAmount);
        uint256 _dividends = SafeMath.div(_ethereum, dividendFeePercent);
        uint256 _taxedEthereum = SafeMath.sub(_ethereum, _dividends);
        return _taxedEthereum;
    }

    /*==========================================
    =            INTERNAL FUNCTIONS            =
    ==========================================*/
    function _processTokenPurchase(uint256 _investmentAmount, address _referredBy) 
        internal 
        returns(uint256) 
    {
        address _investorAddress = msg.sender;
        
        // calculate fees and bonuses
        uint256 _undividedDividends = SafeMath.div(_investmentAmount, dividendFeePercent);
        uint256 _referralBonus = SafeMath.div(_undividedDividends, 3);
        uint256 _dividends = SafeMath.sub(_undividedDividends, _referralBonus);
        uint256 _taxedEthereum = SafeMath.sub(_investmentAmount, _undividedDividends);
        uint256 _tokens = _ethereumToTokens(_taxedEthereum);
        uint256 _fee = _dividends * magnitude;

        require(_tokens > 0 && SafeMath.add(_tokens, totalTokenSupply) > totalTokenSupply);

        // handle referrals
        if (_referredBy != address(0) && 
            _referredBy != _investorAddress && 
            tokenBalanceLedger[_referredBy] >= minimumHoldingRequirement) 
        {
            referralBalance[_referredBy] = SafeMath.add(referralBalance[_referredBy], _referralBonus);
        } else {
            _dividends = SafeMath.add(_dividends, _referralBonus);
            _fee = _dividends * magnitude;
        }

        // update token supply and profit sharing
        if (totalTokenSupply > 0) {
            totalTokenSupply = SafeMath.add(totalTokenSupply, _tokens);
            profitPerShare += (_dividends * magnitude / totalTokenSupply);
            _fee = _fee - (_fee - (_tokens * (_dividends * magnitude / totalTokenSupply)));
        } else {
            totalTokenSupply = _tokens;
        }

        // update investor balance
        tokenBalanceLedger[_investorAddress] = SafeMath.add(
            tokenBalanceLedger[_investorAddress], 
            _tokens
        );

        // update payouts
        int256 _updatedPayouts = (int256)((profitPerShare * _tokens) - _fee);
        payouts[_investorAddress] += _updatedPayouts;

        // fire event
        TokensPurchased(_investorAddress, _investmentAmount, _tokens, _referredBy);

        return _tokens;
    }

    function _ethereumToTokens(uint256 _ethereum) internal view returns(uint256) {
        uint256 _scaledInitialPrice = initialTokenPrice * 1e18;
        
        uint256 _tokensReceived = (
            (
                SafeMath.sub(
                    (
                        _sqrt(
                            (_scaledInitialPrice ** 2) +
                            (2 * (tokenPriceIncrement * 1e18) * (_ethereum * 1e18)) +
                            ((tokenPriceIncrement ** 2) * (totalTokenSupply ** 2)) +
                            (2 * tokenPriceIncrement * _scaledInitialPrice * totalTokenSupply)
                        )
                    ), 
                    _scaledInitialPrice
                )
            ) / (tokenPriceIncrement)
        ) - totalTokenSupply;

        return _tokensReceived;
    }

    function _tokensToEthereum(uint256 _tokens) internal view returns(uint256) {
        uint256 _adjustedTokens = (_tokens + 1e18);
        uint256 _adjustedSupply = (totalTokenSupply + 1e18);
        
        uint256 _ethereumReceived = (
            SafeMath.sub(
                (
                    (
                        (
                            initialTokenPrice + (tokenPriceIncrement * (_adjustedSupply / 1e18))
                        ) - tokenPriceIncrement
                    ) * (_adjustedTokens - 1e18)
                ),
                (tokenPriceIncrement * ((_adjustedTokens ** 2 - _adjustedTokens) / 1e18)) / 2
            ) / 1e18
        );
        
        return _ethereumReceived;
    }

    function _sqrt(uint256 x) internal pure returns (uint256 y) {
        uint256 z = (x + 1) / 2;
        y = x;
        while (z < y) {
            y = z;
            z = (x / z + z) / 2;
        }
    }
}

library SafeMath {
    function mul(uint256 a, uint256 b) internal pure returns (uint256) {
        if (a == 0) {
            return 0;
        }
        uint256 c = a * b;
        assert(c / a == b);
        return c;
    }

    function div(uint256 a, uint256 b) internal pure returns (uint256) {
        uint256 c = a / b;
        return c;
    }

    function sub(uint256 a, uint256 b) internal pure returns (uint256) {
        assert(b <= a);
        return a - b;
    }

    function add(uint256 a, uint256 b) internal pure returns (uint256) {
        uint256 c = a + b;
        assert(c >= a);
        return c;
    }
}
